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
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError) as error:
        raise ValueError("Failed checks must be JSON serializable") from error
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


class RepairGuard:
    """Remember the latest failed grade per module and detect exact repeats."""

    def __init__(
        self, state: Mapping[str, Any] | None = None, *, no_progress_threshold: int = 2
    ) -> None:
        if no_progress_threshold < 1:
            raise ValueError("no_progress_threshold must be positive")
        self.no_progress_threshold = no_progress_threshold
        self._last: dict[str, dict[str, str]] = {}
        # Behavioral history is keyed by module/cell/hardware. It is deliberately
        # separate from `_last`: a layout edit alone is not behavioral progress.
        self._behavior: dict[str, dict[str, Any]] = {}
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
            behavior = state.get("behavior", {})
            if not isinstance(behavior, Mapping):
                raise ValueError("Invalid repair guard state")
            for key, item in behavior.items():
                if not isinstance(key, str) or not isinstance(item, Mapping):
                    raise ValueError("Invalid repair guard state")
                if not isinstance(item.get("history", []), list) or not isinstance(
                    item.get("blocked", {}), Mapping
                ):
                    raise ValueError("Invalid repair guard state")
                # Clamp restored history so malformed/old state cannot grow without bound.
                self._behavior[key] = {
                    "history": item.get("history", [])[-32:],
                    "blocked": dict(item.get("blocked", {})),
                    "relieved": list(item.get("relieved", []))[-16:],
                    "no_progress": dict(item.get("no_progress", {})),
                    "diagnostics": list(item.get("diagnostics", []))[-32:],
                }
            # Keep the total continuation payload bounded as well as each history.
            if len(self._behavior) > 256:
                self._behavior = dict(list(self._behavior.items())[-256:])

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
        return {
            "last": {module: dict(attempt) for module, attempt in self._last.items()},
            "behavior": {
                key: {
                    "history": list(value["history"]),
                    "blocked": dict(value["blocked"]),
                    "relieved": list(value["relieved"]),
                    "no_progress": dict(value["no_progress"]),
                    "diagnostics": list(value.get("diagnostics", [])),
                }
                for key, value in self._behavior.items()
            },
        }

    @staticmethod
    def _behavior_key(module: str, cell: str, hardware_signature: Any) -> str:
        if not module or not cell:
            raise ValueError("Module and cell are required")
        return f"{module}:{cell}:{_json_signature(hardware_signature)}"

    def record_observed_mutation(
        self,
        module: str,
        cell: str,
        hardware_signature: Any,
        before: Any,
        after: Any,
        failed_behavior: Any,
        *,
        completed: bool = True,
        observed_success: bool = True,
    ) -> None:
        """Record a completed, verified mutation and associate it with the failed behavior.

        Incomplete actions and unknown outcomes are ignored. `before`/`after` describe
        the relevant cell behavior, not its layout fingerprint.
        """
        if not completed or not observed_success:
            return
        key = self._behavior_key(module, cell, hardware_signature)
        entry = self._behavior.setdefault(
            key,
            {"history": [], "blocked": {}, "relieved": [], "no_progress": {}, "diagnostics": []},
        )
        if len(self._behavior) > 256:
            self._behavior.pop(next(iter(self._behavior)))
        start, end, behavior = (
            _json_signature(before),
            _json_signature(after),
            _json_signature(failed_behavior),
        )
        hist = entry["history"]
        hist.append(
            {
                "before": start,
                "after": end,
                "before_value": before,
                "after_value": after,
                "behavior": behavior,
            }
        )
        del hist[:-32]
        if start == end:
            count = int(entry["no_progress"].get(behavior, 0)) + 1
            entry["no_progress"][behavior] = count
            if count >= self.no_progress_threshold:
                entry["blocked"][behavior] = (
                    "Repeated completed edits left the observed behavior unchanged."
                )
        else:
            entry["no_progress"][behavior] = 0
            # A -> B -> A shows that edits are cycling rather than converging.
            states = [h["before"] for h in hist[-8:]] + ([hist[-1]["after"]] if hist else [])
            if len(states) >= 3 and states[-1] == states[-3] and states[-2] != states[-1]:
                entry["blocked"][behavior] = (
                    "Observed cell behavior returned to an earlier state (A→B→A)."
                )

    def blocks_repeated_repair(
        self, module: str, cell: str, hardware_signature: Any, failed_behavior: Any
    ) -> bool:
        """Return whether observed successful edits have stalled for this behavior."""
        key = self._behavior_key(module, cell, hardware_signature)
        return _json_signature(failed_behavior) in self._behavior.get(key, {}).get("blocked", {})

    def repair_feedback(
        self, module: str, cell: str, hardware_signature: Any, failed_behavior: Any
    ) -> str | None:
        """Explain a behavioral block and the evidence needed to resume repairs."""
        key = self._behavior_key(module, cell, hardware_signature)
        behavior = _json_signature(failed_behavior)
        reason = self._behavior.get(key, {}).get("blocked", {}).get(behavior)
        if reason is None:
            return None
        history = self._behavior.get(key, {}).get("history", [])[-4:]
        transitions = "; ".join(f"{h.get('before_value')}→{h.get('after_value')}" for h in history)
        return (
            f"Cell {cell}: {reason} Recent observed behavior: {transitions}. "
            "Review the recorded cell history and provide a fresh, "
            "explicit targeted readback of the failed path/control, or a behavioral check "
            "showing improvement, before offering another repair."
        )

    def record_targeted_diagnostic(
        self,
        module: str,
        cell: str,
        hardware_signature: Any,
        failed_behavior: Any,
        evidence: Any,
        *,
        explicit_request: bool,
    ) -> None:
        """Relieve a behavioral block only for an explicitly requested relevant readback.

        Call only for a readback of the failed path/control, never routine placement
        verification or an internal post-action observation.
        """
        if not explicit_request:
            return
        key = self._behavior_key(module, cell, hardware_signature)
        entry = self._behavior.get(key)
        if entry is None:
            return
        behavior = _json_signature(failed_behavior)
        if behavior not in entry["blocked"]:
            return
        evidence_sig = _json_signature(evidence)
        diagnostics = entry.setdefault("diagnostics", [])
        if evidence_sig in diagnostics:
            return
        diagnostics.append(evidence_sig)
        del diagnostics[:-32]
        previous = entry.setdefault("relieved", [])
        token = f"{behavior}:{evidence_sig}"
        if token not in previous:
            previous.append(token)
            del previous[:-16]
            del entry["blocked"][behavior]
            entry["no_progress"][behavior] = 0

    def record_improved_behavior(
        self, module: str, cell: str, hardware_signature: Any, failed_behavior: Any
    ) -> None:
        """Clear a block after a behavioral check demonstrates improvement."""
        key = self._behavior_key(module, cell, hardware_signature)
        entry = self._behavior.get(key)
        if entry is not None:
            behavior = _json_signature(failed_behavior)
            entry.get("blocked", {}).pop(behavior, None)
            entry.get("no_progress", {}).pop(behavior, None)

    def record_module_diagnostic_improvement(
        self, module: str, *, verified_improvement: bool
    ) -> None:
        """Permit a new full grade after fresh targeted behavior improves.

        The caller must have independent relevant evidence. This forgets only
        the identical-grade prohibition, never repair behavior history or module
        acceptance; a complete behavioral suite remains mandatory.
        """
        if verified_improvement is True:
            self._last.pop(module, None)

    def record_module_success(self, module: str) -> None:
        """Forget failed-repair history only after the module behavior really passes."""
        self._last.pop(module, None)
        self._behavior = {
            key: value for key, value in self._behavior.items() if not key.startswith(module + ":")
        }
