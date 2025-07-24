from typing import TypedDict


class LoudnessMeasurement(TypedDict):
    integrated_lufs: float
    truepeak_lufs: float | None
