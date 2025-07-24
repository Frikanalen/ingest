from typing import TypedDict

LoudnessMeasurement = TypedDict("LoudnessMeasurement", {"integrated_lufs": float, "truepeak_lufs": float | None})
