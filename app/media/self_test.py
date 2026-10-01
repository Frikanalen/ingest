"""Proving a worker's encoder works before it claims anything.

A worker with a broken encoder -- no GPU device in the pod, a driver that
opens the device but rejects one option, an image built without the encoder
at all -- does not fail at startup. It fails every job it claims, each one
only after fetching that video's source, and the queue fills with failures
that all look like the video's fault.

So a worker encodes a single frame of synthetic video with the real templates, the
same command a job would run, and checks that what came out is the ladder it
asked for. Anything else and it exits, which in Kubernetes is a pod that will
not come up: the place an operator looks, and nothing is claimed meanwhile.
"""

import asyncio
import shlex
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import replace
from logging import getLogger
from pathlib import Path
from tempfile import TemporaryDirectory

from frikanalen_django_api_client.models import VideoFileVariantEnum

from app.formats import DASH_PREVIEW
from app.media.comand_template import Encoder
from app.media.ffprobe_schema import FfprobeOutput
from app.media.ladder import rungs_for
from app.media.loudness.loudness_measurement import LoudnessMeasurement
from app.media.produce import SourceMedia, render_command
from app.media.segmentation import segmentation_for

logger = getLogger(__name__)

#: Long enough for the CPU backend's ladder on a slow node, short enough that a
#: hung driver is noticed rather than waited on.
TIMEOUT_S = 120

#: One frame. Opening the device and the encoders, with every option the
#: template passes, is where a broken backend fails; a frame through each of
#: them and into the muxer proves the rest of the pipeline is wired up.
CLIP_FRAMES = 1

#: A plausible measurement, so the loudnorm path in the audio chain runs too.
_LOUDNESS = LoudnessMeasurement(
    integrated_lufs=-20.0,
    truepeak_lufs=-3.0,
    loudness_range=5.0,
    threshold_lufs=-30.0,
    target_offset=0.0,
)

_CODEC_PREFIX = {"av1": "av01", "h264": "avc1"}
_MPD = "{urn:mpeg:dash:schema:mpd:2011}"


class SelfTestFailed(RuntimeError):
    """The encoder could not build the formats it would be asked for."""


async def _run(command: str, timeout_s: float) -> None:
    proc = await asyncio.create_subprocess_shell(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout_s)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise SelfTestFailed(f"timed out after {timeout_s}s: {command}") from None
    if proc.returncode != 0:
        tail = stderr.decode(errors="replace")[-2000:]
        raise SelfTestFailed(f"exited {proc.returncode}: {command}\n{tail}")


def _clip_size(encoder: Encoder) -> str:
    # 4K on a GPU, so the 2160p rung is built too: it is the one most likely
    # to be past what the hardware can do. The CPU encoders have no such
    # limit, and a 4K frame through SVT-AV1 costs them about 20s of startup.
    return "1920x1080" if encoder == "cpu" else "3840x2160"


def _clip_command(path: Path, size: str) -> str:
    # Interlaced-flagged, so the deinterlacer in the real templates has
    # something to do; with a tone, so the audio chain does too.
    return (
        f"ffmpeg -v error -y -f lavfi -i testsrc2=size={size}:rate=25 "
        f"-f lavfi -i sine=frequency=440:duration={CLIP_FRAMES / 25} -frames:v {CLIP_FRAMES} "
        "-c:v libx264 -preset ultrafast -pix_fmt yuv420p -flags +ildct+ilme -top 1 -c:a aac "
        f"{shlex.quote(str(path))}"
    )


async def _probe(path: Path) -> FfprobeOutput:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "quiet", "-show_format", "-show_streams", "-of", "json", str(path),
        stdout=subprocess.PIPE,
    )  # fmt: skip
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        raise SelfTestFailed(f"could not probe the test clip {path}")
    return FfprobeOutput.model_validate_json(stdout)


def representations(manifest: Path) -> list[tuple[str, int | None, int | None]]:
    """Each representation's codecs string and size, in manifest order."""
    found = []
    for rep in ET.parse(manifest).getroot().iter(f"{_MPD}Representation"):
        width, height = rep.get("width"), rep.get("height")
        found.append((rep.get("codecs", ""), int(width) if width else None, int(height) if height else None))
    return found


def check_ladder(manifest: Path, source: SourceMedia) -> None:
    """Raise unless the manifest holds exactly the ladder `source` should get."""
    rungs = rungs_for(source.metadata, segmentation_for(source.metadata).frame_rate)
    expected = [(_CODEC_PREFIX[rung.codec], rung.width, rung.height) for rung in rungs]
    if source.has_audio:
        expected.append(("mp4a", None, None))

    found = [(codecs.split(".")[0], width, height) for codecs, width, height in representations(manifest)]
    if found != expected:
        raise SelfTestFailed(f"{manifest} holds {found}, expected {expected}")


async def self_test(encoder: Encoder, work_dir: Path | None = None, timeout_s: float = TIMEOUT_S) -> None:
    """Build the ladder and the preview from a synthetic clip, or raise SelfTestFailed."""
    logger.info("Self-testing the %s encoder", encoder)
    with TemporaryDirectory(dir=work_dir, prefix="self-test-") as scratch_dir:
        scratch = Path(scratch_dir)
        clip = scratch / "clip.mp4"
        await _run(_clip_command(clip, _clip_size(encoder)), timeout_s)

        source = SourceMedia.probed("self-test", clip, await _probe(clip))
        source = replace(source, loudness=_LOUDNESS)

        for file_format in (VideoFileVariantEnum.DASH, DASH_PREVIEW):
            _, manifest, command = render_command(source, file_format, scratch, encoder)
            await _run(command, timeout_s)
            if file_format == VideoFileVariantEnum.DASH:
                check_ladder(manifest, source)
            elif not manifest.is_file():
                raise SelfTestFailed(f"{file_format} produced no {manifest.name}")

    logger.info("The %s encoder passed its self-test", encoder)
