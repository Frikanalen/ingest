"""How a DASH ladder is cut into segments.

The manifest advertises one segment duration for a whole representation, and
players do arithmetic with it: the segment holding time T is at index
T / duration. If the segments are not actually that long, the arithmetic points
at the wrong segment, and the further you seek the further out it lands -- a
seek then fetches the wrong part of the timeline and cannot play until the
player has hunted around for the right one.

Keeping that honest means the segment length has to be something ffmpeg can
hit exactly, which is a whole number of frames rather than a whole number of
seconds. At 59.94fps there is no such thing as a 6.000s segment; the nearest
is 360 frames, which is 6.006s, so that is what both the encoder and the
manifest are told to use.

It also means the frame rate this is all derived from has to be the rate the
ladder is really encoded at, down to the last decimal -- so the rate settled
on here is handed to ffmpeg as `-r` rather than left to it to infer.
"""

import math
from dataclasses import dataclass
from fractions import Fraction

from app.media.ffprobe_schema import FfprobeOutput

#: What we aim a segment at, before rounding to whole frames.
TARGET_SEGMENT_S = 6

#: Used when the source does not say what frame rate it is. Anything outside
#: this range is a malformed file talking nonsense rather than something to
#: encode at that rate -- and since the ladder is encoded CFR, believing a
#: bogus 1000fps would be expensive.
FALLBACK_FRAME_RATE = Fraction(25)
MAX_PLAUSIBLE_FRAME_RATE = Fraction(1000)

#: How far avg_frame_rate may sit from the rate the container declares before
#: the two are describing genuinely different things rather than the same rate
#: measured two ways. A frame-count rounding artefact is parts per million; a
#: variable-rate source is parts per hundred.
SAME_RATE_TOLERANCE = Fraction(1, 1000)


@dataclass(frozen=True)
class Segmentation:
    """A segment length that ffmpeg and the manifest can both agree on."""

    frame_rate: Fraction
    gop_frames: int

    @property
    def segment_duration(self) -> Fraction:
        return Fraction(self.gop_frames) / self.frame_rate

    @property
    def frame_rate_arg(self) -> str:
        """What to hand ffmpeg's `-r`, as an exact ratio rather than a decimal."""
        return f"{self.frame_rate.numerator}/{self.frame_rate.denominator}"

    @property
    def segment_duration_arg(self) -> str:
        """What to hand ffmpeg's `-seg_duration`.

        It has to be the length the encoder will actually produce: ffmpeg
        writes this number into the manifest, so a rounder-looking value here
        just means the manifest lies by the difference.

        Rounded down, never up. ffmpeg ends a segment at the first keyframe at
        or past this length, so a value a hair over the GOP's real length skips
        that keyframe and waits for the next one -- segments come out twice as
        long as the manifest says they are, which is the one error that breaks
        seeking outright. A microsecond short costs nothing.
        """
        micros = math.floor(self.segment_duration * 1_000_000)
        return f"{micros // 1_000_000}.{micros % 1_000_000:06d}"


def _parse_frame_rate(value: str | None) -> Fraction | None:
    if not value:
        return None
    try:
        rate = Fraction(value)
    except (ValueError, ZeroDivisionError):
        return None
    return rate if 0 < rate <= MAX_PLAUSIBLE_FRAME_RATE else None


def _frame_rate_of(stream) -> Fraction | None:
    """The rate to encode this stream at, from the two ffprobe reports of it.

    avg_frame_rate leads: r_frame_rate is the smallest rate that can express
    every frame's timing, which for a variable-rate source is some enormous
    number rather than the rate it plays at.

    But avg_frame_rate is a measurement -- frame count over duration -- and on
    a constant-rate file that lands a hair off the exact ratio the container
    declares, because the two disagree about the last frame. 25fps arrives as
    2295400/91817. Left alone that fraction propagates into a segment length
    of 6.000065s for a GOP that is really 6.000000s, and the manifest ends up
    describing segments that do not exist. Where the measurement agrees with
    the declaration to within a rounding error, the declaration is the truth.
    """
    average = _parse_frame_rate(stream.avg_frame_rate)
    declared = _parse_frame_rate(stream.r_frame_rate)
    if average is None or declared is None:
        return average or declared
    if abs(average - declared) <= SAME_RATE_TOLERANCE * declared:
        return declared
    return average


def segmentation_for(metadata: FfprobeOutput) -> Segmentation:
    """Work out how to cut a source into segments, from what ffprobe saw."""
    rate = None
    for stream in metadata.streams or []:
        if stream.codec_type != "video":
            continue
        rate = _frame_rate_of(stream)
        if rate:
            break

    rate = rate or FALLBACK_FRAME_RATE
    return Segmentation(frame_rate=rate, gop_frames=max(1, round(TARGET_SEGMENT_S * rate)))
