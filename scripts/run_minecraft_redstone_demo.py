"""Recorded, non-benchmark cold versus skill reuse on one redstone task.

Plain invocation (no flags) is unchanged from before this module gained CLI
limits: 12 decisions, 24 primitives, a 120s per-attempt wall clock, a 4,000
token Action cap, a 1,000,000 token Builder cap, one repair, a 600s whole-run
deadline, a 1,100,000 token ceiling, and a 30-call budget.

``--trial`` raises those limits to a profile large enough to plausibly finish
the sample task in one sitting (see ``TRIAL_LIMITS``). ``--escalate`` implies
``--trial`` and, when an attempt does not complete the task, reruns with only
the limit that evidence says was exhausted doubled, up to 4 escalations. Every
escalating run writes one database and JSON per attempt
(``<base-run-id>-a01``, ``-a02``, ...) plus an index JSON listing them all.

This script, its labels, and its budgets are demo-trial tooling authorized by
`hackathon_plan.md` section 26 (26.3/26.4); it is not evaluation or benchmark
code and never feeds a scorecard or comparison.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import re
import sys
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

DEFAULT_CONDITION = "non-benchmark-minecraft-redstone-comparison"
TRIAL_CONDITION = "non-benchmark-minecraft-redstone-demo-trial"


def harness(*, label: str = DEFAULT_CONDITION):
    """Load the easy-mode harness fresh and reshape it into the redstone task.

    Called once per attempt, so overrides from one attempt never leak into
    another: each call re-executes the module from its file.
    """
    name = "redstone_easy_harness"
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).with_name("run_minecraft_easy_live_smoke.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    module.SCENARIO_ID = "redstone-lamp-v1"
    module.CONDITION = label
    module.RESET_COMMANDS = ("function noob_agent_redstone:reset",)
    module.SUCCESS_MESSAGE = "The redstone lamp lights up."
    module.SUCCESS_REASON = "lamp_lit"
    module.EASY_PUBLIC_GOAL = (
        "Make the redstone lamp light up using the supplies in the barrel. "
        "Success is visible when the lamp glows and the message "
        "'The redstone lamp lights up.' appears."
    )
    module.EPISODE_DECISION_BUDGET = 12
    module.EPISODE_PRIMITIVE_BUDGET = 24
    module.UNBOUNDED_WALL_TIME_MS = 120_000
    module.ACTION_MAX_OUTPUT_TOKENS = 4_000
    # Final demo diagnostic: request the maximum explicitly authorized
    # Builder budget. The provider may reject or clip it; that outcome is
    # recorded rather than retried with a smaller request.
    module.BUILDER_MAX_OUTPUT_TOKENS = 1_000_000
    module.MAX_REPAIRS = 1
    return module


def _apply_limits(module: Any, limits: Limits) -> None:
    module.EPISODE_DECISION_BUDGET = limits.decisions
    module.EPISODE_PRIMITIVE_BUDGET = limits.primitives
    module.UNBOUNDED_WALL_TIME_MS = limits.wall_time_ms
    module.ACTION_MAX_OUTPUT_TOKENS = limits.action_cap
    module.BUILDER_MAX_OUTPUT_TOKENS = limits.builder_cap
    module.MAX_REPAIRS = limits.repairs
    # `None` keeps the easy harness's own default (DEFAULT_REPAIR_MAX_OUTPUT_TOKENS,
    # unchanged by this script); a plain DEFAULT_LIMITS run leaves it `None`.
    module.REPAIR_MAX_OUTPUT_TOKENS = limits.repair_cap


# --- Limits: one dataclass for every knob this script exposes --------------


@dataclass(frozen=True)
class Limits:
    """Every limit the redstone demo enforces, as one immutable bundle."""

    decisions: int
    primitives: int
    wall_time_ms: int
    action_cap: int
    builder_cap: int
    repairs: int
    deadline_s: float
    token_ceiling: int
    call_budget: int
    # `None` means: do not override the easy harness's own
    # DEFAULT_REPAIR_MAX_OUTPUT_TOKENS (3,000). Only the trial profile and an
    # explicit `--repair-cap` set this.
    repair_cap: int | None = None

    def escalate(self, kind: LimitKind) -> Limits:
        field = _FIELD_FOR_KIND[kind]
        current = getattr(self, field)
        if current is None:
            # Only repair_cap can be unset, and only escalation from a
            # non-trial run could reach this; treat it as the harness default.
            current = _HARNESS_DEFAULT_REPAIR_CAP
        doubled = double_limit(kind, current)
        value = int(doubled) if isinstance(current, int) else doubled
        return replace(self, **{field: value})


# Today's exact hard-coded values; a plain invocation must stay identical.
DEFAULT_LIMITS = Limits(
    decisions=12,
    primitives=24,
    wall_time_ms=120_000,
    action_cap=4_000,
    builder_cap=1_000_000,
    repairs=1,
    deadline_s=600,
    token_ceiling=1_100_000,
    call_budget=30,
    repair_cap=None,
)

# The demo-trial profile: large enough to plausibly finish the sample task.
# The repair cap defaults to the builder cap, per hackathon_plan.md section 26.4.
TRIAL_LIMITS = Limits(
    decisions=36,
    primitives=72,
    wall_time_ms=360_000,
    action_cap=8_000,
    builder_cap=32_000,
    repairs=3,
    deadline_s=1_800,
    token_ceiling=1_100_000,
    call_budget=90,
    repair_cap=32_000,
)


class LimitKind(StrEnum):
    """Which of the ten limits an attempt exhausted."""

    WALL_TIME = "wall_time"
    DECISION = "decision"
    PRIMITIVE = "primitive"
    ACTION_CAP = "action_cap"
    BUILDER_CAP = "builder_cap"
    REPAIR_CAP = "repair_cap"
    DEADLINE = "deadline"
    TOKEN_CEILING = "token_ceiling"
    CALL_BUDGET = "call_budget"


_FIELD_FOR_KIND: dict[LimitKind, str] = {
    LimitKind.WALL_TIME: "wall_time_ms",
    LimitKind.DECISION: "decisions",
    LimitKind.PRIMITIVE: "primitives",
    LimitKind.ACTION_CAP: "action_cap",
    LimitKind.BUILDER_CAP: "builder_cap",
    LimitKind.REPAIR_CAP: "repair_cap",
    LimitKind.DEADLINE: "deadline_s",
    LimitKind.TOKEN_CEILING: "token_ceiling",
    LimitKind.CALL_BUDGET: "call_budget",
}

# Matches DEFAULT_REPAIR_MAX_OUTPUT_TOKENS in src/noob_agent/prompts/builder.py,
# restated here so escalating a `None` repair_cap has a starting point without
# importing production code into this demo-trial script.
_HARNESS_DEFAULT_REPAIR_CAP = 3_000

# Caps named in hackathon_plan.md section 26.4's escalation approval. A kind
# absent here doubles with no explicit ceiling beyond the 4-escalation limit.
_LIMIT_CAPS: dict[LimitKind, float | None] = {
    LimitKind.ACTION_CAP: 32_000,
    LimitKind.BUILDER_CAP: 128_000,
    LimitKind.REPAIR_CAP: 128_000,
    LimitKind.DEADLINE: 7_200,
    LimitKind.TOKEN_CEILING: 8_000_000,
}


def double_limit(kind: LimitKind, current: float) -> float:
    """Double one limit, clamped to its approved cap (pure function)."""
    doubled = current * 2
    cap = _LIMIT_CAPS.get(kind)
    if cap is not None:
        doubled = min(doubled, cap)
    return doubled


# --- Attempt evidence and classification (pure functions) ------------------


@dataclass(frozen=True)
class AttemptRecord:
    """Evidence extracted from one attempt's stored records, for classification.

    Holds no private fields: only the same stop reasons, finish reasons, and
    token/call totals a public record already carries.
    """

    stop_reasons: frozenset[str]
    action_finish_reasons: tuple[str | None, ...]
    # The build call's finish_reason, and the last repair call's, kept apart:
    # a truncated repair must double the (separately capped) repair budget,
    # never the build budget, and vice versa.
    build_finish_reason: str | None
    repair_finish_reason: str | None = None
    # The Builder's own outcome.stop_reason ("truncated_reply", "unusable_reply",
    # "accepted", "repair_budget_exhausted", "learning_budget_exhausted",
    # "validated", or None if the Builder never ran).
    builder_stop_reason: str | None = None
    run_status: str = "completed"
    budget_exhausted: str | None = None  # "token", "call", or None
    builder_accepted: bool = False
    reuse_lit: bool = False
    # Public validation rejection summaries (check/code/message), read from the
    # repair prompt this attempt sent; never a private grader predicate.
    validation_rejections: tuple[str, ...] = ()

    @classmethod
    def refused(cls, run_status: str = "refused") -> AttemptRecord:
        """A preflight refusal: nothing ran, so nothing is classifiable."""
        return cls(
            stop_reasons=frozenset(),
            action_finish_reasons=(),
            build_finish_reason=None,
            repair_finish_reason=None,
            builder_stop_reason=None,
            run_status=run_status,
            budget_exhausted=None,
            builder_accepted=False,
            reuse_lit=False,
            validation_rejections=(),
        )


def attempt_completed(record: AttemptRecord) -> bool:
    """Completion = the Builder accepted a skill AND reuse lit the lamp.

    A lit cold lamp is not required.
    """
    return record.builder_accepted and record.reuse_lit


def _action_truncated(reasons: tuple[str | None, ...]) -> bool:
    if not reasons:
        return False
    if reasons[-1] == "length":
        return True
    truncated = sum(1 for reason in reasons if reason == "length")
    return truncated * 2 > len(reasons)


def classify_exhausted_limit(record: AttemptRecord) -> LimitKind | None:
    """Which single limit to double next, or ``None`` if nothing is classifiable.

    Priority mirrors hackathon_plan.md section 26.4's approval, in order:
    a per-attempt wall-time stop, a decision or primitive stop, a truncated
    Action reply, a truncated Builder reply, the whole-run deadline, then the
    token or call ceiling.
    """
    if attempt_completed(record):
        return None
    if "wall_time_limit" in record.stop_reasons:
        return LimitKind.WALL_TIME
    if "decision_limit" in record.stop_reasons:
        return LimitKind.DECISION
    if "primitive_limit" in record.stop_reasons:
        return LimitKind.PRIMITIVE
    if _action_truncated(record.action_finish_reasons):
        return LimitKind.ACTION_CAP
    # A repair's own truncation is checked first: it can only happen after a
    # build that finished cleanly, and doubling the build cap again (as a
    # combined "last builder call" check once did) never helps it (live
    # evidence a02/a03).
    if record.repair_finish_reason == "length":
        return LimitKind.REPAIR_CAP
    if record.build_finish_reason == "length":
        return LimitKind.BUILDER_CAP
    if record.run_status in {"TimeoutError", "CancelledError"}:
        return LimitKind.DEADLINE
    if record.budget_exhausted == "token":
        return LimitKind.TOKEN_CEILING
    if record.budget_exhausted == "call":
        return LimitKind.CALL_BUDGET
    return None


def is_retryable_builder_failure(record: AttemptRecord) -> bool:
    """Whether a fresh attempt at the *same* limits is worth trying.

    `unusable_reply` means the model's reply could not be parsed into a skill
    package (for example invalid JSON or a missing fenced block) even though
    it finished normally (`finish_reason=stop`) -- live evidence a04. That says
    nothing about any limit being too small, so nothing is doubled; only a
    fresh attempt, still counted toward the escalation cap.
    """
    return record.builder_stop_reason == "unusable_reply"


_FAILING_CHECKS_RE = re.compile(
    r"Failing checks:\n\n(.*?)\n\nThe rejected source was:", re.DOTALL
)
_ISSUE_LINE_RE = re.compile(r"^- (\[.+)$", re.MULTILINE)


def extract_validation_rejections(repair_prompt_text: str) -> tuple[str, ...]:
    """Pull the public validation-issue summaries out of a stored repair prompt.

    `render_repair_prompt` (src/noob_agent/prompts/builder.py) always writes a
    "Failing checks:" section listing only public check/code/message text --
    never a private grader predicate or held-out data -- before "The rejected
    source was:". This reads that same text back from the stored prompt,
    without importing or re-running any production code.
    """
    match = _FAILING_CHECKS_RE.search(repair_prompt_text)
    if match is None:
        return ()
    return tuple(_ISSUE_LINE_RE.findall(match.group(1)))


# --- Escalation driver: pure orchestration over an injected attempt runner --


@dataclass(frozen=True)
class AttemptOutcome:
    attempt_id: str
    limits: Limits
    record: AttemptRecord
    payload: dict[str, Any]


def run_escalating_attempts(
    run_one: Callable[[str, Limits], AttemptOutcome],
    *,
    base_run_id: str,
    limits: Limits,
    escalate: bool,
    max_escalations: int = 4,
) -> list[AttemptOutcome]:
    """Run one attempt, then, if escalating, keep doubling the exhausted limit.

    ``run_one`` does all the I/O; this function only decides whether to run
    again and with what limits, so it is unit-testable with a fake runner.
    """
    attempts: list[AttemptOutcome] = []
    current = limits
    attempt_number = 1
    while True:
        attempt_id = f"{base_run_id}-a{attempt_number:02d}" if escalate else base_run_id
        outcome = run_one(attempt_id, current)
        attempts.append(outcome)
        if not escalate or attempt_completed(outcome.record):
            break
        if attempt_number > max_escalations:
            break
        kind = classify_exhausted_limit(outcome.record)
        if kind is not None:
            current = current.escalate(kind)
        elif is_retryable_builder_failure(outcome.record):
            pass  # Same limits: an unusable reply says no limit was too small.
        else:
            break
        attempt_number += 1
    return attempts


# --- CLI ---------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decisions", type=int, default=None)
    parser.add_argument("--primitives", type=int, default=None)
    parser.add_argument("--wall-time-ms", type=int, default=None)
    parser.add_argument("--action-cap", type=int, default=None)
    parser.add_argument("--builder-cap", type=int, default=None)
    parser.add_argument("--repair-cap", type=int, default=None)
    parser.add_argument("--repairs", type=int, default=None)
    parser.add_argument("--deadline-s", type=float, default=None)
    parser.add_argument("--token-ceiling", type=int, default=None)
    parser.add_argument("--call-budget", type=int, default=None)
    parser.add_argument(
        "--trial",
        action="store_true",
        help="Use the demo-trial limit profile (decisions=36, primitives=72, ...).",
    )
    parser.add_argument(
        "--escalate",
        action="store_true",
        help=(
            "Imply --trial; double the exhausted limit and rerun on "
            "non-completion, up to 4 times."
        ),
    )
    args = parser.parse_args(argv)
    if args.escalate:
        args.trial = True
    return args


def _limits_from_args(args: argparse.Namespace) -> Limits:
    base = TRIAL_LIMITS if args.trial else DEFAULT_LIMITS
    overrides = {
        "decisions": args.decisions,
        "primitives": args.primitives,
        "wall_time_ms": args.wall_time_ms,
        "action_cap": args.action_cap,
        "builder_cap": args.builder_cap,
        "repair_cap": args.repair_cap,
        "repairs": args.repairs,
        "deadline_s": args.deadline_s,
        "token_ceiling": args.token_ceiling,
        "call_budget": args.call_budget,
    }
    changed = {field: value for field, value in overrides.items() if value is not None}
    return replace(base, **changed) if changed else base


# --- One attempt ---------------------------------------------------------


async def run(
    environ: Mapping[str, str] | None = None,
    *,
    limits: Limits = DEFAULT_LIMITS,
    run_id: str | None = None,
    label: str = DEFAULT_CONDITION,
    attempt_sink: dict[str, Any] | None = None,
) -> int:
    m = harness(label=label)
    _apply_limits(m, limits)
    environment = os.environ if environ is None else environ
    settings = m.IntegrationSettings.from_environ(environment)
    if not settings.trace.enabled or not settings.model.inference_model:
        print(
            "Refusing to run: configure a model provider, model ID, and Weave tracing "
            "first (see .env.demo.example).",
            file=sys.stderr,
        )
        return 2
    if not settings.wandb.api_key:
        print(
            "Refusing to run: WANDB_API_KEY is not set. Copy .env.demo.example to .env "
            "and add your key from https://wandb.ai/authorize.",
            file=sys.stderr,
        )
        return 2
    run_id = run_id or datetime.now(UTC).strftime("minecraft-redstone-%Y%m%dT%H%M%SZ")
    database = Path(f".noob-agent/{run_id}.sqlite3")
    database.parent.mkdir(exist_ok=True)
    trace = m.build_trace_sink(settings.trace, wandb=settings.wandb)
    summary: dict[str, Any] = {}
    status = "completed"
    resets = 0

    class DemoConnector(m.MinecraftConnector):
        async def reset(self, scenario_id, seed):
            nonlocal resets
            observation = await super().reset(scenario_id, seed)
            resets += 1
            phase = "COLD: no learned skill." if resets == 1 else "LOOP: fresh attempt with skill."
            await self.announce(f"[noob:Doing] {phase}")
            return observation

    try:
        with m.EpisodeStore.open(database) as store:
            client = m.EasyBudgetedClient(
                m.build_model_client(settings.model, settings.wandb),
                store,
                run_id,
                call_budget=limits.call_budget,
                token_budget=limits.token_ceiling,
            )
            try:
                async with asyncio.timeout(limits.deadline_s):
                    await m._run_easy(
                        store=store,
                        client=client,
                        model_id=settings.model.inference_model,
                        executor=m.build_skill_executor(settings.sandbox),
                        trace=trace,
                        run_id=run_id,
                        summary=summary,
                        connector_factory=lambda: DemoConnector(m._easy_settings()),
                    )
            except (TimeoutError, asyncio.CancelledError) as error:
                status = type(error).__name__
            except Exception as error:
                status = type(error).__name__
                summary["error"] = str(error)
            records = m._records_for_run(store, run_id)
            costs: dict[str, int] = {}
            for call in records:
                phase = (
                    "builder"
                    if call.purpose in {"build", "repair"}
                    else ("reuse" if call.experiment_id.endswith("-reuse") else "cold")
                )
                costs[phase] = costs.get(phase, 0) + m._call_tokens(call)
            calls_used = len(records)
            tokens_used = sum(m._call_tokens(call) for call in records)

            training_stop = (summary.get("training") or {}).get("stop_reason")
            reuse_info = summary.get("reuse")
            reuse_stop = reuse_info.get("stop_reason") if reuse_info else None
            stop_reasons = frozenset(
                reason for reason in (training_stop, reuse_stop) if reason is not None
            )
            action_finish_reasons = tuple(
                call.finish_reason for call in records if call.purpose == "action"
            )
            build_records = [call for call in records if call.purpose == "build"]
            repair_records = [call for call in records if call.purpose == "repair"]
            build_finish_reason = build_records[0].finish_reason if build_records else None
            repair_finish_reason = repair_records[-1].finish_reason if repair_records else None
            builder_stop_reason = (summary.get("builder") or {}).get("stop_reason")
            validation_rejections = tuple(
                rejection
                for call in repair_records
                for rejection in extract_validation_rejections(call.prompt)
            )
            budget_exhausted = None
            if status == "EasyBudgetExhausted":
                if calls_used >= limits.call_budget:
                    budget_exhausted = "call"
                elif tokens_used >= limits.token_ceiling:
                    budget_exhausted = "token"
            builder_accepted = bool((summary.get("builder") or {}).get("accepted"))
            cold_lit = (summary.get("training") or {}).get("terminal_reason") == m.SUCCESS_REASON
            reuse_lit = False
            if reuse_info and reuse_info.get("episode_id"):
                with suppress(Exception):
                    stored_reuse = store.read_episode(reuse_info["episode_id"])
                    reuse_lit = bool(
                        stored_reuse.outcome is not None and stored_reuse.outcome.terminal
                    )

            payload = {
                "run_id": run_id,
                "label": label,
                "status": status,
                "database": str(database),
                "comparison": "same-task cold versus skill reuse; not held-out transfer",
                "limits": limits.__dict__,
                "tokens_by_phase": costs,
                "cold_lamp_lit": cold_lit,
                "reuse_lamp_lit": reuse_lit,
                "builder_stop_reason": builder_stop_reason,
                "build_finish_reason": build_finish_reason,
                "repair_finish_reason": repair_finish_reason,
                "validation_rejections": list(validation_rejections),
                "sequence": summary,
            }
            output = database.with_suffix(".json")
            output.write_text(json.dumps(payload, indent=2) + "\n")
            print(json.dumps(payload, indent=2), flush=True)
            if attempt_sink is not None:
                attempt_sink["payload"] = payload
                attempt_sink["record"] = AttemptRecord(
                    stop_reasons=stop_reasons,
                    action_finish_reasons=action_finish_reasons,
                    build_finish_reason=build_finish_reason,
                    repair_finish_reason=repair_finish_reason,
                    builder_stop_reason=builder_stop_reason,
                    run_status=status,
                    budget_exhausted=budget_exhausted,
                    builder_accepted=builder_accepted,
                    reuse_lit=reuse_lit,
                    validation_rejections=validation_rejections,
                )
    finally:
        trace.flush()
    return 0


# --- Multi-attempt driver and index -----------------------------------


def _index_payload(base_run_id: str, attempts: list[AttemptOutcome]) -> dict[str, Any]:
    return {
        "base_run_id": base_run_id,
        "label": TRIAL_CONDITION,
        "attempts": [
            {
                "attempt_id": outcome.attempt_id,
                "limits": outcome.limits.__dict__,
                "status": outcome.payload.get("status"),
                "tokens_by_phase": outcome.payload.get("tokens_by_phase"),
                "cold_lamp_lit": outcome.payload.get("cold_lamp_lit"),
                "reuse_lamp_lit": outcome.payload.get("reuse_lamp_lit"),
                "skill": ((outcome.payload.get("sequence") or {}).get("builder") or {}).get(
                    "skill"
                ),
                "skill_uses": ((outcome.payload.get("sequence") or {}).get("reuse") or {}).get(
                    "skill_uses"
                ),
                "completed": attempt_completed(outcome.record),
                "builder_stop_reason": outcome.record.builder_stop_reason,
                "build_finish_reason": outcome.record.build_finish_reason,
                "repair_finish_reason": outcome.record.repair_finish_reason,
                "validation_rejections": list(outcome.record.validation_rejections),
                # Set when this attempt itself was an unusable-reply retry at
                # unchanged limits, not a limit escalation.
                "builder_retry_reason": (
                    "unusable_reply" if is_retryable_builder_failure(outcome.record) else None
                ),
            }
            for outcome in attempts
        ],
        "completed": bool(attempts) and attempt_completed(attempts[-1].record),
    }


def main(argv: list[str] | None = None, *, environ: dict[str, str] | None = None) -> int:
    args = _parse_args(argv)
    limits = _limits_from_args(args)
    label = TRIAL_CONDITION if args.trial else DEFAULT_CONDITION
    base_run_id = datetime.now(UTC).strftime("minecraft-redstone-%Y%m%dT%H%M%SZ")
    refusal_code: int | None = None

    def run_one(attempt_id: str, attempt_limits: Limits) -> AttemptOutcome:
        nonlocal refusal_code
        sink: dict[str, Any] = {}
        code = asyncio.run(
            run(
                environ,
                limits=attempt_limits,
                run_id=attempt_id,
                label=label,
                attempt_sink=sink,
            )
        )
        if code == 2 or "record" not in sink:
            refusal_code = code
            return AttemptOutcome(
                attempt_id,
                attempt_limits,
                AttemptRecord.refused(),
                sink.get("payload", {"run_id": attempt_id, "status": "refused"}),
            )
        return AttemptOutcome(attempt_id, attempt_limits, sink["record"], sink["payload"])

    attempts = run_escalating_attempts(
        run_one, base_run_id=base_run_id, limits=limits, escalate=args.escalate
    )
    if refusal_code is not None:
        return refusal_code
    if args.escalate:
        index_path = Path(f".noob-agent/{base_run_id}-index.json")
        index_path.parent.mkdir(exist_ok=True)
        index_path.write_text(json.dumps(_index_payload(base_run_id, attempts), indent=2) + "\n")
        print(json.dumps(_index_payload(base_run_id, attempts), indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
