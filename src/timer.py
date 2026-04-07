"""Einfacher Timer für Timing-Ausgaben."""

import time


class Timer:
    def __init__(self):
        self._start = time.monotonic()
        self._lap = self._start

    def elapsed(self) -> str:
        secs = time.monotonic() - self._start
        return self._fmt(secs)

    def lap(self, label: str) -> str:
        now = time.monotonic()
        secs = now - self._lap
        total = now - self._start
        self._lap = now
        msg = f"  ⏱  {label}: {self._fmt(secs)}  (gesamt: {self._fmt(total)})"
        print(msg)
        return msg

    @staticmethod
    def _fmt(secs: float) -> str:
        if secs < 60:
            return f"{secs:.1f}s"
        mins = int(secs // 60)
        rest = secs % 60
        return f"{mins}m {rest:.1f}s"
