from dataclasses import dataclass, field


@dataclass
class FfmpegProgressParser:
    """Turns an ffmpeg `-progress pipe:1` key=value stream into a 0..1 fraction.

    ffmpeg reports position within whatever pass is currently running, not
    the job as a whole. A template that chains several ffmpeg invocations
    (two-pass encoding) declares how many via `passes`, so a pass boundary
    -- signalled by a `progress=end` line -- advances the overall fraction
    by 1/passes instead of resetting it to zero. Passes are weighted
    equally; there is no way to know from the stream alone that, say, the
    analysis pass of a two-pass encode usually runs faster than the second.
    """

    duration_s: float
    passes: int = 1
    _completed_passes: int = field(default=0, init=False)
    _current_out_time_s: float = field(default=0.0, init=False)

    def feed_line(self, line: str) -> float | None:
        """Feed one line of -progress output.

        Returns the updated overall fraction when the line moves it,
        `None` otherwise (most lines, e.g. `fps=`, `bitrate=`, are noise
        for our purposes).
        """
        key, sep, value = line.strip().partition("=")
        if not sep:
            return None

        if key == "out_time_us":
            out_time_us = self._parse_non_negative_int(value)
            if out_time_us is None:
                return None
            self._current_out_time_s = out_time_us / 1_000_000
            return self._fraction()

        if key == "progress" and value == "end":
            self._completed_passes = min(self._completed_passes + 1, self.passes)
            self._current_out_time_s = 0.0
            return self._fraction()

        return None

    def _fraction(self) -> float:
        if self.duration_s <= 0:
            return 0.0
        pass_fraction = min(self._current_out_time_s / self.duration_s, 1.0)
        return min((self._completed_passes + pass_fraction) / self.passes, 1.0)

    @staticmethod
    def _parse_non_negative_int(value: str) -> int | None:
        # Before ffmpeg has a real position it reports N/A, or a huge
        # negative sentinel; both are "no update", not "0".
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None
