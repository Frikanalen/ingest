"""Building one derived format from a source file, and publishing it.

Lifted out of Ingester so that both callers run the same code: a fresh upload,
and a backfill of something that has been in the archive for years. The two
differ only in where the source file came from, and what happens to it
afterwards must not depend on that -- a transcode path that existed in two
copies is how the catalogue came to disagree with itself in the first place.
"""

from dataclasses import dataclass
from logging import getLogger
from pathlib import Path, PurePosixPath

from frikanalen_django_api_client.models import VideoFileVariantEnum

from app.archive_store import ArchiveSession
from app.django_client.service import DjangoApiService
from app.media.comand_template import ProfileTemplateArguments, TemplatedCommandGenerator
from app.media.ffprobe_schema import FfprobeOutput
from app.media.loudness.loudness_measurement import LoudnessMeasurement
from app.media.segmentation import segmentation_for
from app.runner import ProgressCallback, Task
from app.util.file_name_utils import derived_file_location


class FormatProductionError(RuntimeError):
    """A format could not be produced from a source that was fine."""


class TranscodeFailed(FormatProductionError):
    """ffmpeg did not produce the output."""


class PublishFailed(FormatProductionError):
    """The output was produced but could not be put in the archive."""


@dataclass(frozen=True)
class SourceMedia:
    """A video's source file, and what probing it told us.

    Assembled once per video and reused for every format, because each of these
    costs a decode of the whole file and none of them differ between formats.
    """

    video_id: str
    path: Path
    metadata: FfprobeOutput
    #: Whether the source has an audio track at all. A template that would
    #: otherwise declare an empty audio output has to leave it out entirely.
    has_audio: bool
    #: Measured from the source, where we have a measurement. None means the
    #: analysis pass found nothing to work from, and a template that would
    #: normalize has to pass the audio through untouched instead.
    loudness: LoudnessMeasurement | None = None

    @property
    def duration_s(self) -> float:
        return float(self.metadata.format.duration)

    @classmethod
    def probed(cls, video_id: str, path: Path, metadata: FfprobeOutput) -> "SourceMedia":
        """Read what ffprobe saw. Loudness is measured separately and added after."""
        return cls(
            video_id=video_id,
            path=path,
            metadata=metadata,
            has_audio=any(stream.codec_type == "audio" for stream in metadata.streams or []),
        )


class FormatProducer:
    """Turns a source file into one archived, registered format at a time.

    Holds an open archive session, so one connection carries every format of a
    video rather than one apiece.
    """

    def __init__(self, archive: ArchiveSession, django_api: DjangoApiService):
        self.archive = archive
        self.django_api = django_api
        self.logger = getLogger(__name__)

    async def produce(
        self,
        source: SourceMedia,
        file_format: VideoFileVariantEnum,
        scratch: Path,
        on_progress: ProgressCallback | None = None,
    ) -> PurePosixPath:
        """Build `file_format`, archive it, register it, and say where it went."""
        self.logger.info("Processing %s as %s", source.path, file_format)

        template = TemplatedCommandGenerator(file_format)

        # Each format gets a directory of its own, and whatever the command
        # leaves in it is what gets archived. A format can therefore produce a
        # set of files -- DASH writes a manifest and the media it names --
        # without ingest having to know the shape of any of them.
        output_dir = scratch / str(file_format)
        output_dir.mkdir()
        output_file = output_dir / template.metadata.output_name_for(source.path)

        segmentation = segmentation_for(source.metadata)

        template_args = ProfileTemplateArguments(
            input_file=source.path,
            output_file=output_file,
            output_dir=output_dir,
            scratch_dir=scratch,
            seek_s=(source.duration_s * 0.25 or 30),
            has_audio=source.has_audio,
            loudness=source.loudness,
            gop_frames=segmentation.gop_frames,
            segment_duration_s=segmentation.segment_duration_arg,
            frame_rate=segmentation.frame_rate_arg,
        )

        command = template.render(template_args)
        self.logger.debug("Generated command: %s", command)

        try:
            await Task(
                command,
                duration_s=source.duration_s,
                passes=template.metadata.passes,
                on_progress=on_progress,
            ).execute()
        except Exception as e:
            self.logger.error("Failed to produce %s: %s", file_format, e)
            raise TranscodeFailed(str(e)) from e

        destination = derived_file_location(source.video_id, str(file_format), output_file)
        self.logger.info("Storing %s to %s", file_format, destination)
        try:
            await self._publish(output_dir, output_file, source.video_id, file_format)
        except Exception as e:
            self.logger.error("Failed to archive %s: %s", file_format, e)
            raise PublishFailed(str(e)) from e

        # Registered last, and only once every file is in the archive. That
        # ordering is what lets a row's existence stand for "this format landed
        # in full", and what makes the revision it carries safe to trust.
        self.logger.info("Creating video file entry for %s", destination)
        await self.django_api.create_video_file(
            filename=str(destination),
            file_format=file_format,
            video_id=source.video_id,
            profile_revision=template.metadata.revision,
        )
        return destination

    async def _publish(
        self,
        output_dir: Path,
        primary: Path,
        video_id: str,
        file_format: VideoFileVariantEnum,
    ) -> None:
        """Copy everything one format produced into the archive, primary last.

        The archive is exported read-only to the playout hosts, so a manifest
        arriving before the media it references would be briefly readable and
        broken. Publishing it last means the format either is not there yet or
        is there in full.
        """
        outputs = sorted(output_dir.iterdir(), key=lambda output: output == primary)

        if nested := [output for output in outputs if not output.is_file()]:
            # derived_file_location flattens to a basename, so a template that
            # nested its output would silently archive to the wrong paths.
            raise NotImplementedError(f"{file_format} produced directories, which cannot be archived: {nested}")

        for output in outputs:
            await self.archive.put(output, derived_file_location(video_id, str(file_format), output))
