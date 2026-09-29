"""Deterministic guard against ineffective public-check repair cycles.

Integration: after a failed module grade, call :meth:`RepairGuard.record` with
the module name, failed-check records, verified module layout fingerprint, and
declaration/test plan. Before spending another grade, call
:meth:`RepairGuard.blocks_regrade` with the current evidence; it blocks only
when failure, declaration/test plan, and verified layout all match the last
recorded failure. Persist :meth:`to_state` in continuation state and restore
with :meth:`from_state` so the guard survives loop continuation.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any


def _signature(failed_checks: Sequence[Mapping[str, Any]]) -> str:
    return _json_signature(list(failed_checks))


def _json_signature(value: Any) -> str:
    try:
        canonical = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
    except (TypeError, ValueError) as error:
        raise ValueError("Failed checks must be JSON serializable") from error
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


class RepairGuard:
    """Remember the latest failed grade per module and detect exact repeats."""

    def __init__(self, state: Mapping[str, Any] | None = None) -> None:
        self._last: dict[str, dict[str, str]] = {}
        if state is not None:
            raw = state.get("last", {})
            if not isinstance(raw, Mapping):
                raise ValueError("Invalid repair guard state")
            for module, attempt in raw.items():
                if (
                    not isinstance(module, str)
                    or not isinstance(attempt, Mapping)
                    or not isinstance(attempt.get("failure_signature"), str)
                    or not isinstance(attempt.get("declaration_signature"), str)
                    or not isinstance(attempt.get("layout_fingerprint"), str)
                ):
                    raise ValueError("Invalid repair guard state")
                self._last[module] = {
                    "failure_signature": attempt["failure_signature"],
                    "declaration_signature": attempt["declaration_signature"],
                    "layout_fingerprint": attempt["layout_fingerprint"],
                }

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> RepairGuard:
        """Restore a guard from its JSON-compatible state representation."""
        return cls(state)

    def blocks_regrade(
        self,
        module: str,
        failed_checks: Sequence[Mapping[str, Any]],
        layout_fingerprint: str,
        declaration: Any = None,
    ) -> bool:
        """Whether this module's identical failed grade has no verified change."""
        if not module or not isinstance(layout_fingerprint, str):
            raise ValueError("Module and layout fingerprint are required")
        previous = self._last.get(module)
        return previous == {
            "failure_signature": _signature(failed_checks),
            "declaration_signature": _json_signature(declaration),
            "layout_fingerprint": layout_fingerprint,
        }

    def record(
        self,
        module: str,
        failed_checks: Sequence[Mapping[str, Any]],
        layout_fingerprint: str,
        declaration: Any = None,
    ) -> None:
        """Record a failed grade using its verified layout and check evidence."""
        if not module or not isinstance(layout_fingerprint, str):
            raise ValueError("Module and layout fingerprint are required")
        self._last[module] = {
            "failure_signature": _signature(failed_checks),
            "declaration_signature": _json_signature(declaration),
            "layout_fingerprint": layout_fingerprint,
        }

    def to_state(self) -> dict[str, Any]:
        """Return JSON-compatible state suitable for a loop continuation."""
        return {"last": {module: dict(attempt) for module, attempt in self._last.items()}}
