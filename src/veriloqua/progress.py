"""User-facing progress: plain-language "waiting" lines shown on stderr while the
tiered engine works — e.g. *开始检测源语言…* / *提取专有名词与术语…* — instead of raw
backend call counters like ``claude_cli(haiku) call #1``.

Two primitives:

* :func:`working` — a context manager that rotates a list of "in progress" phrases on
  a timer while a slow step runs. The first phrase prints at once; later ones appear
  one per ``interval`` so a fast call shows a single line and a slow one walks the list.
* :func:`note` — one status line for something the engine *actually* observed
  (detected slang, source ambiguity, too few web sources, …).

Silent unless stderr is a TTY (or ``VERILOQUA_PROGRESS`` is set), and fully muted by
``VERILOQUA_QUIET`` — so pipes, JSON output, and the test suite stay clean, and no
thread is ever spawned when nobody is watching.
"""

from __future__ import annotations

import contextlib
import os
import sys
import threading
from collections.abc import Iterator, Sequence

_PREFIX = "· "  # a light bullet marks these as status, not translation output


def show_progress() -> bool:
    if os.environ.get("VERILOQUA_QUIET"):
        return False
    return bool(os.environ.get("VERILOQUA_PROGRESS")) or sys.stderr.isatty()


def note(message: str) -> None:
    """Print one status line, if progress is visible."""
    if show_progress():
        sys.stderr.write(f"{_PREFIX}{message}\n")
        sys.stderr.flush()


@contextlib.contextmanager
def working(phrases: Sequence[str], *, interval: float = 2.5) -> Iterator[None]:
    """Rotate "waiting" phrases on a background timer while the wrapped step runs.

    No-op — and zero threads — when progress is muted or ``phrases`` is empty."""
    if not show_progress() or not phrases:
        yield
        return

    stop = threading.Event()

    def _run() -> None:
        note(phrases[0])
        for phrase in phrases[1:]:
            if stop.wait(interval):  # woken early → the step already finished
                return
            note(phrase)

    ticker = threading.Thread(target=_run, daemon=True)
    ticker.start()
    try:
        yield
    finally:
        stop.set()
        ticker.join(timeout=0.2)
