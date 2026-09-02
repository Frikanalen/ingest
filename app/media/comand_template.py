from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import TypedDict

import yaml
from jinja2 import Template
from pydantic import BaseModel, Field, model_validator

from app.media.loudness.loudness_measurement import LoudnessMeasurement

#: Where the format templates live.
#:
#: Resolved through `importlib.resources` against the `app` package, so the
#: templates travel with the code that reads them rather than with a working
#: directory or a checkout.
#:
#: They used to be found by forking `git rev-parse --show-toplevel`, with a
#: bare `except` falling back to a hardcoded `/app`. That was right in the
#: image only by coincidence, and by either of two: the build copies `.git` in
#: with the source and the base image ships git, so the fork answers `/app` --
#: and where it cannot, the fallback is the same `/app`, because that is also
#: the WORKDIR. Nothing checked which, so nothing would have noticed either
#: one going away. Resolving against the package leaves nothing to be
#: accidentally right about, and costs no fork on the event loop to do it.
TEMPLATE_DIR = resources.files("app") / "templates"


class ProfileMetadata(BaseModel):
    #: Extension of an output named after the source file, e.g. `webm`.
    output_file_extension: str | None = None
    #: A fixed output filename, for a format whose output is referenced by
    #: name from a manifest and so cannot follow the source file's stem.
    output_file_name: str | None = None
    #: How many ffmpeg invocations this template chains (two-pass encoding
    #: templates emit two `-progress` streams back to back). Used to turn
    #: ffmpeg's per-pass progress into progress across the whole command.
    passes: int = 1
    #: Which iteration of this profile the template currently describes.
    #: Recorded against every file it produces, so that output made by an
    #: earlier iteration can be found and replaced without inspecting it.
    #:
    #: Bump it whenever a change makes existing output worth rebuilding -- a
    #: different ladder, a corrected keyframe interval -- and leave it alone
    #: for a change that does not, such as a comment or a rename. Numbering
    #: starts at 1 because 0 is reserved for files registered before any of
    #: this existed; see app.formats.UNTRACKED_REVISION.
    revision: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _names_its_output_exactly_one_way(self) -> "ProfileMetadata":
        if bool(self.output_file_extension) == bool(self.output_file_name):
            raise ValueError("a template needs either output_file_extension or output_file_name, not both or neither")
        return self

    def output_name_for(self, source_file: Path) -> str:
        """What this template's primary output is called for a given source."""
        return self.output_file_name or f"{source_file.stem}.{self.output_file_extension}"


class ProfileTemplateArguments(TypedDict):
    input_file: Path
    #: The format's primary output: the file registered with django-api, and
    #: for a multi-file format the manifest that names the rest.
    output_file: Path
    #: Everything left in here once the command succeeds is archived, so a
    #: template must not put intermediates in it -- see scratch_dir.
    output_dir: Path
    #: Working space for anything that must not reach the archive, such as
    #: two-pass log files. Discarded with the rest of the job's scratch.
    scratch_dir: Path
    seek_s: int
    #: Whether the source has an audio track at all. A template that would
    #: otherwise declare an empty audio output has to leave it out entirely:
    #: an adaptation set with no representation in it is not valid DASH.
    has_audio: bool
    #: The rate to encode at, as an exact ratio ("25/1"). Set explicitly so
    #: that gop_frames really is segment_duration_s long: left to infer it,
    #: ffmpeg may not pick the rate those two were worked out from.
    frame_rate: str
    #: Keyframe interval, in frames. Set explicitly because libvpx places
    #: keyframes of its own otherwise, and a segmented format can only cut
    #: where a keyframe is -- see app.media.segmentation.
    gop_frames: int
    #: Segment length in seconds, as ffmpeg spells it. Must be what
    #: gop_frames actually comes to, since ffmpeg copies it into the manifest.
    segment_duration_s: str
    #: The source's measured loudness, where we have one. None means the
    #: analysis pass found nothing to work from, and a template that would
    #: normalize has to pass the audio through at its original level
    #: instead -- a wrong gain is worse than no gain.
    loudness: LoudnessMeasurement | None


class TemplateNotFound(FileNotFoundError):
    """A format has no template under `TEMPLATE_DIR`.

    Raised rather than resolved to whatever a fallback path happens to hold:
    the templates are the single source of truth for `current_revision`, so a
    lookup that quietly found the wrong directory would answer "is this format
    stale" about output nobody built.
    """


def _shipped_formats() -> list[str]:
    """Which formats do have a template, for the benefit of the error message."""
    try:
        return sorted(entry.name.removesuffix(".j2") for entry in TEMPLATE_DIR.iterdir() if entry.name.endswith(".j2"))
    except OSError:
        return []


def _read_template_file(format_name: str) -> str:
    template = TEMPLATE_DIR / f"{format_name}.j2"
    try:
        # Explicit encoding: the final image has no locale set, so the default
        # would be ASCII there and UTF-8 on every developer machine.
        return template.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError) as e:
        # Anything else -- a permission error, say -- is left to propagate as
        # itself; those already name the path they failed on.
        raise TemplateNotFound(
            f"no template for format {format_name!r}: {template} does not exist. "
            f"{TEMPLATE_DIR} holds {_shipped_formats() or 'no templates at all'}."
        ) from e


def _split_content_and_metadata(content: str) -> tuple[str, str]:
    """Splits the content into metadata and template body."""
    parts = content.split("---", 2)
    if len(parts) != 3:
        raise ValueError("Template content must contain exactly one YAML block followed by the template body.")
    return parts[1], parts[2]


@lru_cache
def _load_template(format_name: str) -> tuple[ProfileMetadata, Template]:
    """Read and compile one format's template.

    Cached because a generator is constructed per format per video -- a
    catalogue-wide backfill asks for the same handful of templates thousands of
    times -- and neither the file nor the compiled template varies between
    those calls. A Jinja template holds no state across renders, so one
    compiled template is safe to hand to every caller.
    """
    metadata, body = _split_content_and_metadata(_read_template_file(format_name))
    return ProfileMetadata(**yaml.safe_load(metadata)), Template(body.replace("\n", ""))


class TemplatedCommandGenerator:
    def __init__(self, format_name: str):
        # str() so that the enum member and its value are one cache key rather
        # than two.
        self.template_metadata, self.template = _load_template(str(format_name))

    @property
    def metadata(self) -> ProfileMetadata:
        return self.template_metadata

    def render(self, args: ProfileTemplateArguments) -> str:
        return self.template.render(**args)
