"""The Doom side-by-side demo recorder's pure timeline and labelling helpers.

The recorder is non-benchmark demo tooling: it plays cold and skill-assisted
held-out episodes and stitches their captured frames into one video. These
tests cover only the parts that decide which frame appears when, so the video's
clocks show real elapsed time.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

SCRIPT = Path("scripts/record_doom_comparison.py")


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("record_doom_comparison", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_frame_index_holds_the_latest_frame_at_or_before_time() -> None:
    recorder = _load()
    times = [0.0, 0.5, 2.0]

    assert recorder.frame_index_at(times, 0.0) == 0
    assert recorder.frame_index_at(times, 0.49) == 0
    assert recorder.frame_index_at(times, 0.5) == 1
    assert recorder.frame_index_at(times, 1.9) == 1
    assert recorder.frame_index_at(times, 99.0) == 2


def test_frame_index_before_the_first_frame_shows_the_first_frame() -> None:
    recorder = _load()

    assert recorder.frame_index_at([1.0, 2.0], 0.2) == 0


def test_output_frame_count_covers_the_longer_side_plus_a_hold() -> None:
    recorder = _load()

    assert recorder.output_frame_count(3.0, 7.5, fps=10, hold_seconds=2.0) == 95


def test_clock_label_shows_tenths_of_a_second() -> None:
    recorder = _load()

    assert recorder.clock_label(0.0) == "0.0 s"
    assert recorder.clock_label(12.345) == "12.3 s"


def test_verdict_label_reports_goal_time_or_stop_reason() -> None:
    recorder = _load()

    assert recorder.verdict_label(True, "terminal_state", 8.24) == "GOAL COMPLETE in 8.2 s"
    assert recorder.verdict_label(False, "decision_limit", 30.0) == "FAILED: decision_limit"
