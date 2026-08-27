"""What the DASH manifest claims, checked against the media it describes.

A manifest states one segment length for a whole representation and players
find the segment holding time T by dividing. If the segments are not that
length the arithmetic drifts, and a seek fetches a part of the timeline that
does not contain what was asked for -- so the player stalls and hunts. None of
that shows up in a command-line assertion; it only shows up in the bytes.
"""

import re
import shutil
import struct
import subprocess
from pathlib import Path

import pytest
from frikanalen_django_api_client.models import VideoFileVariantEnum

from app.media.comand_template import ProfileTemplateArguments, TemplatedCommandGenerator
from app.media.ffprobe_schema import FfprobeOutput
from app.media.segmentation import segmentation_for

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")

# 59.94fps: the rate that has no whole-second segment length, and the one the
# first real upload to hit this was shot at.
BROADCAST_RATE = "60000/1001"


def _probe(path: Path) -> FfprobeOutput:
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return FfprobeOutput.model_validate_json(out.stdout)


def _segment_durations(representation: str, media: bytes) -> list[float]:
    """Read each segment's real duration out of its sidx box."""
    durations = []
    for start, end in re.findall(r'indexRange="(\d+)-(\d+)"', representation):
        sidx = media[int(start) : int(end) + 1]
        version = sidx[8]
        timescale = struct.unpack(">I", sidx[16:20])[0]
        offset = 12 + 4 + 4 + (16 if version == 1 else 8) + 4
        durations.append(struct.unpack(">I", sidx[offset + 4 : offset + 8])[0] / timescale)
    return durations


@pytest.fixture(scope="module")
def dash_output(tmp_path_factory) -> Path:
    work = tmp_path_factory.mktemp("dash")
    source = work / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=320x180:rate={BROADCAST_RATE}:duration=40",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(source),
            "-y",
        ],
        check=True,
    )

    output_dir = work / "out"
    output_dir.mkdir()
    segmentation = segmentation_for(_probe(source))
    command = TemplatedCommandGenerator(VideoFileVariantEnum.DASH).render(
        ProfileTemplateArguments(
            input_file=source,
            output_file=output_dir / "manifest.mpd",
            output_dir=output_dir,
            scratch_dir=work,
            seek_s=1,
            has_audio=False,
            loudness=None,
            gop_frames=segmentation.gop_frames,
            segment_duration_s=segmentation.segment_duration_arg,
        )
    )
    subprocess.run(command, shell=True, check=True, capture_output=True)
    return output_dir / "manifest.mpd"


def test_every_representation_has_evenly_spaced_segments(dash_output):
    manifest = dash_output.read_text()
    representations = re.findall(r'<Representation id="\d+".*?</Representation>', manifest, re.S)
    assert representations

    for representation in representations:
        media = (dash_output.parent / re.search(r"<BaseURL>([^<]+)</BaseURL>", representation).group(1)).read_bytes()
        durations = _segment_durations(representation, media)
        # The last segment is whatever is left over, so it is exempt.
        assert len(durations) > 2, "need several segments for this to mean anything"
        assert max(durations[:-1]) - min(durations[:-1]) < 0.001, f"uneven segments: {durations}"


def test_the_declared_segment_length_matches_the_real_one(dash_output):
    """The regression this file exists for. A manifest saying 6.000s over
    segments that are really 6.006s is 84 seconds out by the end of an hour."""
    manifest = dash_output.read_text()

    for representation in re.findall(r'<Representation id="\d+".*?</Representation>', manifest, re.S):
        declared = int(re.search(r'<SegmentList timescale="(\d+)" duration="(\d+)"', representation).group(2)) / 1e6
        media = (dash_output.parent / re.search(r"<BaseURL>([^<]+)</BaseURL>", representation).group(1)).read_bytes()
        durations = _segment_durations(representation, media)

        assert declared == pytest.approx(durations[0], abs=0.001), (
            f"manifest declares {declared}s but segments are {durations[0]}s"
        )


def test_renditions_share_segment_boundaries(dash_output):
    """Switching rendition mid-playback only works where the boundaries line up."""
    manifest = dash_output.read_text()
    per_rendition = []
    for representation in re.findall(r'<Representation id="\d+".*?</Representation>', manifest, re.S):
        media = (dash_output.parent / re.search(r"<BaseURL>([^<]+)</BaseURL>", representation).group(1)).read_bytes()
        per_rendition.append(_segment_durations(representation, media))

    assert all(durations == per_rendition[0] for durations in per_rendition)
