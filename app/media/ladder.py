"""What the DASH ladder is, independently of what encodes it.

Every encoder backend has a template of its own -- see
`app.media.comand_template.Encoder` -- but they all build this ladder. Keeping
the rungs here rather than spelled out in each template is what makes "the
same ladder on QSV, NVENC and CPU" true by construction: a template loops over
what it is handed and only decides which encoder and scaler produce each rung.

AV1 is the ladder proper. H.264 is a fallback for players with no AV1 decoder
(older Apple hardware, older smart TVs), so it is deliberately short: two
rungs, topping out at 720p.
"""

import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

from app.media.ffprobe_schema import FfprobeOutput

Codec = Literal["av1", "h264"]

#: How far a rung's bitrate may peak above its average. DASH advertises a
#: bandwidth per representation and the player's switching logic trusts it, so
#: every rung is capped rather than left to a quality target alone.
MAXRATE_FACTOR = Fraction(3, 2)

#: The rate-control buffer, as a multiple of the average bitrate.
BUFSIZE_FACTOR = 2

#: Above this frame rate, motion costs more bits than the base ladder allows
#: for, so the larger rungs get `HIGH_FRAME_RATE_UPLIFT` on top.
HIGH_FRAME_RATE_THRESHOLD = Fraction(30)
HIGH_FRAME_RATE_UPLIFT = Fraction(5, 4)
HIGH_FRAME_RATE_MIN_HEIGHT = 720


@dataclass(frozen=True)
class RungSpec:
    """One rung as declared: what it is aiming for before any source is seen."""

    codec: Codec
    height: int
    bitrate_k: int


#: The ladder, highest first within each codec. AV1 first, so its
#: representations are the lower stream indices.
LADDER: tuple[RungSpec, ...] = (
    RungSpec("av1", 1080, 3500),
    RungSpec("av1", 720, 2000),
    RungSpec("av1", 540, 1100),
    RungSpec("av1", 360, 500),
    RungSpec("h264", 720, 3000),
    RungSpec("h264", 360, 800),
)


@dataclass(frozen=True)
class Rung:
    """One rung as a template renders it: exact output size and rates."""

    codec: Codec
    width: int
    height: int
    bitrate_k: int

    @property
    def maxrate_k(self) -> int:
        return math.ceil(self.bitrate_k * MAXRATE_FACTOR)

    @property
    def bufsize_k(self) -> int:
        return self.bitrate_k * BUFSIZE_FACTOR


def _even(value: float) -> int:
    return max(2, math.ceil(value / 2) * 2)


def padded_size(width: int, height: int) -> tuple[int, int]:
    """The frame every rung is scaled from: the source padded out to 16:9.

    Mirrors the templates' `pad` filter exactly, which pads whichever way the
    source is short of 16:9 and rounds both sides up to even.
    """
    return _even(max(width, height * 16 / 9)), _even(max(height, width * 9 / 16))


def _source_size(metadata: FfprobeOutput) -> tuple[int, int] | None:
    for stream in metadata.streams or []:
        if stream.codec_type == "video" and stream.width and stream.height:
            return stream.width, stream.height
    return None


def rungs_for(metadata: FfprobeOutput, frame_rate: Fraction) -> list[Rung]:
    """The ladder as it applies to one source.

    No rung is upscaled past the padded source: a rung taller than the source
    is encoded at the source's height instead, as the VP9 ladder did. Its
    bitrate is left alone, so a small source gets a short ladder of bitrate
    variants rather than fewer representations.

    Sizes are worked out here rather than by scaler expressions in the
    templates, because the hardware scalers do not all accept the same
    expressions, and the self-test needs to know what to expect anyway.
    """
    source = _source_size(metadata)
    padded_w, padded_h = padded_size(*source) if source else (1920, 1080)

    rungs = []
    for spec in LADDER:
        height = min(spec.height, padded_h)
        width = _even(height * padded_w / padded_h)
        bitrate = spec.bitrate_k
        if frame_rate > HIGH_FRAME_RATE_THRESHOLD and spec.height >= HIGH_FRAME_RATE_MIN_HEIGHT:
            bitrate = math.ceil(bitrate * HIGH_FRAME_RATE_UPLIFT)
        rungs.append(Rung(spec.codec, width, _even(height), bitrate))
    return rungs
