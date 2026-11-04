"""How much memory a run uses, in its log: a runner that runs out is killed with its log, so the last line says how close it was."""
from __future__ import annotations

import resource
import threading
from pathlib import Path

_GB = 1024 ** 3
_INTERVAL_SECONDS = 60


def available_gb(meminfo: str | None = None) -> float | None:
    """The machine's available memory; None where it cannot be told (a Mac in development)."""
    try:
        text = meminfo if meminfo is not None else Path("/proc/meminfo").read_text()
        kilobytes = next(int(line.split()[1]) for line in text.splitlines() if line.startswith("MemAvailable:"))
    except (OSError, StopIteration, ValueError, IndexError):
        return None
    return kilobytes * 1024 / _GB


def process_gb(pid: int, statm: str | None = None) -> float:
    """A process's resident memory; 0 for one that has ended."""
    try:
        text = statm if statm is not None else Path(f"/proc/{pid}/statm").read_text()
        return int(text.split()[1]) * resource.getpagesize() / _GB
    except (OSError, ValueError, IndexError):
        return 0.0


def peak_gb() -> float:
    """The most this process has held at once (Linux reports kilobytes)."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024 / _GB


class MemoryWatch:
    """A line a minute for the run: what the machine has left, and what each company process holds."""

    def __init__(self, pool, log=print, interval: float = _INTERVAL_SECONDS):
        self._pool = pool
        self._log = log
        self._interval = interval
        self._stop = threading.Event()
        self.lowest_available_gb: float | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> "MemoryWatch":
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def sample(self) -> None:
        available = available_gb()
        if available is None:
            return
        self.lowest_available_gb = available if self.lowest_available_gb is None else min(self.lowest_available_gb, available)
        held = sorted((process_gb(pid) for pid in list(getattr(self._pool, "_processes", None) or {})), reverse=True)
        self._log(f"Memory: {available:.1f} GB available; company processes hold {', '.join(f'{gb:.1f}' for gb in held) or 'none'} GB")

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            self.sample()
