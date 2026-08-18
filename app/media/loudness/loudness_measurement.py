from pydantic import BaseModel


class LoudnessMeasurement(BaseModel):
    """What one EBU R.128 analysis pass found in a source file.

    Only finite measurements get this far -- see `parse_loudness`, which
    treats an unmeasurable track as no measurement at all.

    The first two fields are what django-api stores against the `original`
    videofile, and what playout works from to hit its own -23 LUFS target.
    The rest exist so the DASH encode can normalize in a single linear pass:
    handed back to `loudnorm` as its `measured_*` inputs, they turn a filter
    that would otherwise ride the gain dynamically into a constant offset.
    """

    integrated_lufs: float
    #: dBTP, and None for a track with no measurable peak -- the column is
    #: nullable, and -inf is not a JSON number.
    truepeak_lufs: float | None
    loudness_range: float
    threshold_lufs: float
    #: The gain loudnorm says it would still need after the linear offset.
    target_offset: float
