import json
import logging
import math
import re
from pathlib import Path

from app.media.loudness.loudness_measurement import LoudnessMeasurement
from app.runner import Task

logger = logging.getLogger(__name__)

#: loudnorm writes its report to stderr, prefixed by a `[Parsed_loudnorm_0 @
#: ...]` line and followed by nothing in particular. The last balanced block
#: of braces in the stream is the report.
_JSON_BLOCK = re.compile(r"\{[^{}]*\}")

#: Our field names against loudnorm's, for the values that must be finite
#: for the measurement to mean anything.
_REPORT_FIELDS = {
    "integrated_lufs": "input_i",
    "loudness_range": "input_lra",
    "threshold_lufs": "input_thresh",
    "target_offset": "target_offset",
}


def loudness_command(input_file: Path) -> str:
    """An analysis-only pass over a file's first audio stream.

    `-vn` matters more than it looks: without it ffmpeg decodes every video
    frame on the way to the audio it is actually measuring, which on a long
    upload costs about as much as one of the DASH renditions.
    """
    return f'ffmpeg -nostats -hide_banner -i "{input_file}" -vn -map 0:a:0 -af loudnorm=print_format=json -f null -'


def parse_loudness(stderr: str) -> LoudnessMeasurement | None:
    """Pull the loudnorm report out of an analysis pass's stderr.

    Returns None for a track that could not be measured. loudnorm reports
    every value as a string and reaches for `-inf`/`inf` on digital silence,
    which is neither a number django-api can store nor a gain any encode
    should be asked to apply.
    """
    blocks = _JSON_BLOCK.findall(stderr)
    if not blocks:
        logger.warning("No loudnorm report found in ffmpeg output")
        return None

    try:
        report = json.loads(blocks[-1])
        measured = {field: float(report[key]) for field, key in _REPORT_FIELDS.items()}
        truepeak = float(report["input_tp"])
    except (ValueError, KeyError):
        logger.warning("Could not read loudnorm report: %r", blocks[-1], exc_info=True)
        return None

    if not all(math.isfinite(value) for value in measured.values()):
        logger.info("Track has no measurable loudness: %r", blocks[-1])
        return None

    return LoudnessMeasurement(
        truepeak_lufs=truepeak if math.isfinite(truepeak) else None,
        **measured,
    )


async def measure_loudness(input_file: Path) -> LoudnessMeasurement | None:
    """Measure a file's loudness, or return None if it cannot be measured.

    Never raises. A missing measurement costs the DASH output its
    normalization and leaves playout to fall back on its own levelling,
    which is a far smaller loss than failing an otherwise good ingest over
    an audio track ffmpeg would not analyze.
    """
    try:
        _, stderr = await Task(loudness_command(input_file)).execute()
    except ChildProcessError:
        logger.warning("Loudness analysis of %s failed", input_file, exc_info=True)
        return None

    return parse_loudness(stderr)
