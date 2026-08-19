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
"""

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


@dataclass(frozen=True)
class Segmentation:
    """A segment length that ffmpeg and the manifest can both agree on."""

    frame_rate: Fraction
    gop_frames: int

    @property
    def segment_duration(self) -> Fraction:
        return Fraction(self.gop_frames) / self.frame_rate

    @property
    def segment_duration_arg(self) -> str:
        """What to hand ffmpeg's `-seg_duration`.

        It has to be the length the encoder will actually produce: ffmpeg
        writes this number into the manifest, so a rounder-looking value here
        just means the manifest lies by the difference.
        """
        return f"{float(self.segment_duration):.6f}"


def _parse_frame_rate(value: str | None) -> Fraction | None:
    if not value:
        return None
    try:
        rate = Fraction(value)
    except (ValueError, ZeroDivisionError):
        return None
    return rate if 0 < rate <= MAX_PLAUSIBLE_FRAME_RATE else None


def segmentation_for(metadata: FfprobeOutput) -> Segmentation:
    """Work out how to cut a source into segments, from what ffprobe saw."""
    rate = None
    for stream in metadata.streams or []:
        if stream.codec_type != "video":
            continue
        # avg_frame_rate first: r_frame_rate is the smallest rate that can
        # express every frame's timing, which for a variable-rate source is
        # some enormous number rather than the rate it plays at.
        rate = _parse_frame_rate(stream.avg_frame_rate) or _parse_frame_rate(stream.r_frame_rate)
        if rate:
            break

    rate = rate or FALLBACK_FRAME_RATE
    return Segmentation(frame_rate=rate, gop_frames=max(1, round(TARGET_SEGMENT_S * rate)))
