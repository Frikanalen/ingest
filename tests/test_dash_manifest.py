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

# What ffprobe measured on a 25fps conference recording whose frame count and
# duration disagree about the final frame. 24.99973fps: near enough to 25 to
# look like the rounding artefact it is, far enough to describe segments that
# no encoder will produce.
NEAR_MISS_RATE = "2295400/91817"


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


def _make_source(path: Path, rate: str, duration: int) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=320x180:rate={rate}:duration={duration}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(path),
            "-y",
        ],
        check=True,
    )
    return path


def _build_dash(work: Path, source: Path, segmentation) -> Path:
    output_dir = work / "out"
    output_dir.mkdir()
    command = TemplatedCommandGenerator(VideoFileVariantEnum.DASH).render(
        ProfileTemplateArguments(
            input_file=source,
            output_file=output_dir / "manifest.mpd",
            output_dir=output_dir,
            scratch_dir=work,
            seek_s=1,
            has_audio=False,
            loudness=None,
            frame_rate=segmentation.frame_rate_arg,
            gop_frames=segmentation.gop_frames,
            segment_duration_s=segmentation.segment_duration_arg,
        )
    )
    subprocess.run(command, shell=True, check=True, capture_output=True)
    return output_dir / "manifest.mpd"


@pytest.fixture(scope="module")
def dash_output(tmp_path_factory) -> Path:
    work = tmp_path_factory.mktemp("dash")
    source = _make_source(work / "source.mp4", BROADCAST_RATE, 40)
    return _build_dash(work, source, segmentation_for(_probe(source)))


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


def test_a_measured_rate_a_hair_off_the_real_one_still_segments_honestly(tmp_path):
    """The regression a real upload arrived with.

    ffmpeg ends a segment at the first keyframe at or past -seg_duration. The
    source's measured 25.0000544fps put that at 6.000065s for a GOP that is
    really 6.000000s, so every other keyframe was a hair too early to cut on:
    segments came out 12s long while the manifest went on declaring 6.000065s.
    Players locate a segment by dividing, so every seek landed in the wrong
    half of the timeline and stalled there.
    """
    source = _make_source(tmp_path / "source.mp4", "25", 20)
    probe = _probe(source)
    probe.streams[0].avg_frame_rate = NEAR_MISS_RATE

    segmentation = segmentation_for(probe)
    manifest_path = _build_dash(tmp_path, source, segmentation)
    manifest = manifest_path.read_text()

    for representation in re.findall(r'<Representation id="\d+".*?</Representation>', manifest, re.S):
        declared = int(re.search(r'<SegmentList timescale="(\d+)" duration="(\d+)"', representation).group(2)) / 1e6
        media = (manifest_path.parent / re.search(r"<BaseURL>([^<]+)</BaseURL>", representation).group(1)).read_bytes()
        durations = _segment_durations(representation, media)

        assert len(durations) > 2, f"segments came out {durations[0]}s long, so there are only {len(durations)}"
        assert declared == pytest.approx(durations[0], abs=0.001), (
            f"manifest declares {declared}s but segments are {durations[0]}s"
        )
