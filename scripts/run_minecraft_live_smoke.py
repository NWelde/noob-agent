"""Run the non-benchmark Minecraft learning-loop smoke test against the local server."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

from noob_agent.agents.action import ActionAgent
from noob_agent.agents.builder import BuilderAgent
from noob_agent.agents.evidence import select_evidence
from noob_agent.connectors.minecraft import MinecraftConnector
from noob_agent.domain.records import ExperimentRecord, ModelCallRecord
from noob_agent.models.client import ModelClient, ModelRequest, ModelResponse, build_model_client
from noob_agent.models.recording import RecordingModelClient
from noob_agent.observability.tracing import build_trace_sink
from noob_agent.runtime.runner import EpisodeRunner
from noob_agent.settings import IntegrationSettings
from noob_agent.skills.executor import build_skill_executor
from noob_agent.skills.registry import SkillRegistry
from noob_agent.skills.runtime import SkillRuntime
from noob_agent.storage import EpisodeStore

SCENARIO_ID = "resonator-training-v1"
SEED = 20260912
CONDITION = "non-benchmark-minecraft-live-smoke"
DEADLINE_SECONDS = 600.0
TOKEN_BUDGET = 2_000_000
CALL_BUDGET = 500
PRIMITIVE_BUDGET = 1_000
MAX_REPAIRS = 5
LIVE_SMOKE_ACTION_MAX_OUTPUT_TOKENS = 3_000
SMOKE_CYCLE_DECISION_BUDGET = 12
SMOKE_CYCLE_PRIMITIVE_BUDGET = 24
SMOKE_CYCLE_WALL_TIME_MS = 90_000
STALE_TARGET_FAILURE_LIMIT = 2
NO_PROGRESS_ACTION_LIMIT = 5
MINECRAFT_CHAT_LIMIT = 240


class RunOptions(NamedTuple):
    database: Path | None
    sequence_id: str | None
    live_smoke: bool
    deadline_seconds: float
    token_budget: int
    call_budget: int
    primitive_budget: int
    max_repairs: int


class SmokeBudgetExhausted(asyncio.CancelledError):
    """A new provider request would exceed the declared smoke-run budget."""


def _chat_transcript_lines(
    *,
    purpose: str,
    system: str | None = None,
    prompt: str | None = None,
    reasoning: str | None = None,
    reply: str | None = None,
    error: str | None = None,
) -> tuple[str, ...]:
    """Render every model-call field into short, first-person chat lines."""
    fields = (
        ("system", system),
        ("prompt", prompt),
        ("reasoning", reasoning),
        ("reply", reply),
        ("error", error),
    )
    lines: list[str] = []
    for label, value in fields:
        if value is None:
            continue
        rendered = " ".join(value.split()) or "<empty>"
        # Leave ample room for the label and sequence prefix below Minecraft's
        # 256-character chat limit.
        chunks = [rendered[index : index + 180] for index in range(0, len(rendered), 180)]
        for index, chunk in enumerate(chunks, start=1):
            lines.append(f"[noob:{purpose} {label} {index}/{len(chunks)}] {chunk}")
    return tuple(lines)


class MinecraftChatTranscriptClient:
    """Mirror a smoke-run model call to Minecraft chat without changing it."""

    def __init__(self, inner: ModelClient, connector: MinecraftConnector, *, purpose: str) -> None:
        self._inner = inner
        self._connector = connector
        self._purpose = purpose
        self.provider = getattr(inner, "provider", "unknown")

    async def _announce(self, lines: tuple[str, ...]) -> None:
        for line in lines:
            try:
                await self._connector.announce(line)
            except Exception:
                # Narration is useful for a live demo but must never alter the
                # model request, action result, or run outcome.
                return

    async def complete(self, request: ModelRequest) -> ModelResponse:
        await self._announce(
            _chat_transcript_lines(
                purpose=self._purpose, system=request.system, prompt=request.prompt
            )
        )
        try:
            response = await self._inner.complete(request)
        except Exception as error:
            await self._announce(
                _chat_transcript_lines(
                    purpose=self._purpose,
                    error=f"{type(error).__name__}: {error}",
                )
            )
            raise
        await self._announce(
            _chat_transcript_lines(
                purpose=self._purpose,
                reasoning=response.reasoning,
                reply=response.text,
            )
        )
        return response


class SmokeStopTracker:
    """Detect unproductive live exploration without exposing private state."""

    def __init__(self) -> None:
        self._stale_targets = 0
        self._no_progress = 0

    def record(self, *, code: str, state_changed: bool | None) -> bool:
        self._stale_targets = self._stale_targets + 1 if code == "NO_VISIBLE_TARGET" else 0
        self._no_progress = 0 if state_changed else self._no_progress + 1
        return (
            self._stale_targets >= STALE_TARGET_FAILURE_LIMIT
            or self._no_progress >= NO_PROGRESS_ACTION_LIMIT
        )


class SmokeActionAgent(ActionAgent):
    """Action agent with smoke-only public no-progress stopping."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._tracker = SmokeStopTracker()
        self._stop_reason: str | None = None

    @property
    def stop_reason(self) -> str | None:
        return self._stop_reason

    def notice(self, request: object, outcome: object) -> None:
        super().notice(request, outcome)
        if getattr(outcome, "code", None) is None:
            return
        if self._tracker.record(
            code=str(outcome.code), state_changed=getattr(outcome, "state_changed", None)
        ):
            self._stop_reason = (
                "repeated_failure" if outcome.code == "NO_VISIBLE_TARGET" else "no_progress"
            )


def _parse_args(argv: Sequence[str] | None = None) -> RunOptions:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=None)
    parser.add_argument("--sequence-id", default=None)
    parser.add_argument("--live-smoke", action="store_true")
    parsed = parser.parse_args(argv)
    if not parsed.live_smoke:
        parser.error("--live-smoke is required; this runner is not an evaluation command")
    return RunOptions(
        database=parsed.database,
        sequence_id=parsed.sequence_id,
        live_smoke=True,
        deadline_seconds=DEADLINE_SECONDS,
        token_budget=TOKEN_BUDGET,
        call_budget=CALL_BUDGET,
        primitive_budget=PRIMITIVE_BUDGET,
        max_repairs=MAX_REPAIRS,
    )


def _database_for(options: RunOptions, run_id: str) -> Path:
    return options.database or Path(f".noob-agent/{run_id}.sqlite3")


def _condition_for(options: RunOptions) -> str:
    del options
    return CONDITION


def _cycle_seeds(index: int) -> tuple[int, int]:
    """Unique public bookkeeping seeds; the training pack layout stays fixed."""
    training_seed = SEED + index * 2
    return training_seed, training_seed + 1


def _smoke_experiment(
    *, experiment_id: str, model_id: str, connector_version: str, created_at: datetime
) -> ExperimentRecord:
    return ExperimentRecord(
        experiment_id=experiment_id,
        model_id=model_id,
        condition=CONDITION,
        connector_version=connector_version,
        decision_budget=SMOKE_CYCLE_DECISION_BUDGET,
        primitive_budget=SMOKE_CYCLE_PRIMITIVE_BUDGET,
        wall_time_budget_ms=SMOKE_CYCLE_WALL_TIME_MS,
        created_at=created_at,
    )


def _call_tokens(record: ModelCallRecord) -> int:
    if record.input_tokens is not None and record.output_tokens is not None:
        return record.input_tokens + record.output_tokens
    return record.max_output_tokens + (len(record.system) + len(record.prompt)) // 4


def _records_for_run(store: EpisodeStore, run_id: str) -> tuple[ModelCallRecord, ...]:
    return tuple(
        record
        for record in store.read_model_calls()
        if record.experiment_id.startswith(f"{run_id}-s")
    )


class _BudgetedClient:
    def __init__(self, inner: ModelClient, store: EpisodeStore, run_id: str) -> None:
        self._inner, self._store, self._run_id = inner, store, run_id
        self.provider = getattr(inner, "provider", "unknown")

    async def complete(self, request: ModelRequest) -> ModelResponse:
        records = _records_for_run(self._store, self._run_id)
        if len(records) >= CALL_BUDGET:
            raise SmokeBudgetExhausted("Model-call budget exhausted; request was not sent.")
        if sum(_call_tokens(record) for record in records) >= TOKEN_BUDGET:
            raise SmokeBudgetExhausted("Token budget exhausted; request was not sent.")
        return await self._inner.complete(request)


async def _run_sequence(
    *,
    store: EpisodeStore,
    client: ModelClient,
    model_id: str,
    executor: object,
    trace: object,
    sequence_id: str,
    cycle_index: int,
    settings: IntegrationSettings,
) -> dict[str, object]:
    training_seed, reuse_seed = _cycle_seeds(cycle_index)
    connector = MinecraftConnector()
    manifest = await connector.manifest()
    created_at = datetime.now(UTC)
    training_experiment = _smoke_experiment(
        experiment_id=f"{sequence_id}-training",
        model_id=model_id,
        connector_version=manifest.connector_version,
        created_at=created_at,
    )
    store.create_experiment(training_experiment)
    registry = SkillRegistry()
    cold_client = RecordingModelClient(
        MinecraftChatTranscriptClient(client, connector, purpose="action"),
        store=store,
        experiment_id=training_experiment.experiment_id,
        role="action",
        model_id=model_id,
        trace=trace,
    )
    cold = EpisodeRunner(
        connector=connector,
        store=store,
        policy=SmokeActionAgent(
            cold_client,
            manifest=manifest,
            max_output_tokens=LIVE_SMOKE_ACTION_MAX_OUTPUT_TOKENS,
        ),
        trace=trace,
    )
    cold_result = await cold.run(
        experiment=training_experiment,
        scenario_id=SCENARIO_ID,
        seed=training_seed,
        split="training",
    )
    stored = store.read_episode(cold_result.episode_id)
    builder = BuilderAgent(
        RecordingModelClient(
            MinecraftChatTranscriptClient(client, connector, purpose="builder"),
            store=store,
            experiment_id=training_experiment.experiment_id,
            role="builder",
            model_id=model_id,
            episode_id=cold_result.episode_id,
            trace=trace,
        ),
        registry,
        executor=executor,
        max_repairs=MAX_REPAIRS,
        max_output_tokens=settings.model.builder_max_output_tokens,
    )
    outcome = await builder.build(
        select_evidence(stored),
        training_trace=stored,
        primitive_names=tuple(tool.name for tool in manifest.tools),
        authoring_model_id=model_id,
        created_at=datetime.now(UTC),
    )
    reuse: dict[str, object] | None = None
    if outcome.accepted:
        reuse_connector = MinecraftConnector()
        reuse_manifest = await reuse_connector.manifest()
        reuse_experiment = _smoke_experiment(
            experiment_id=f"{sequence_id}-reuse",
            model_id=model_id,
            connector_version=reuse_manifest.connector_version,
            created_at=datetime.now(UTC),
        )
        store.create_experiment(reuse_experiment)
        reuse_runner = EpisodeRunner(
            connector=reuse_connector,
            store=store,
            policy=SmokeActionAgent(
                RecordingModelClient(
                    MinecraftChatTranscriptClient(client, reuse_connector, purpose="action"),
                    store=store,
                    experiment_id=reuse_experiment.experiment_id,
                    role="action",
                    model_id=model_id,
                    trace=trace,
                ),
                manifest=reuse_manifest,
                skills=registry.available_skills(),
                max_output_tokens=LIVE_SMOKE_ACTION_MAX_OUTPUT_TOKENS,
            ),
            trace=trace,
            skills=SkillRuntime(executor, available=registry.available_skills()),
        )
        reuse_result = await reuse_runner.run(
            experiment=reuse_experiment,
            scenario_id=SCENARIO_ID,
            seed=reuse_seed,
            split="validation",
        )
        reuse = {
            "episode_id": reuse_result.episode_id,
            "stop_reason": reuse_result.stop_reason,
            "decisions_used": reuse_result.decisions_used,
            "primitives_used": reuse_result.primitives_used,
            "skill_uses": len(reuse_result.skill_uses),
        }
    return {
        "sequence_id": sequence_id,
        "training": {
            "episode_id": cold_result.episode_id,
            "stop_reason": cold_result.stop_reason,
            "decisions_used": cold_result.decisions_used,
            "primitives_used": cold_result.primitives_used,
        },
        "builder": {
            "accepted": outcome.accepted,
            "stop_reason": outcome.stop_reason,
            "attempts": outcome.attempts,
        },
        "reuse": reuse,
    }


def main(argv: Sequence[str] | None = None, *, environ: dict[str, str] | None = None) -> int:
    args = _parse_args(argv)
    settings = IntegrationSettings.from_environ(os.environ if environ is None else environ)
    if not settings.trace.enabled:
        print("Refusing live smoke: Weave tracing must be enabled.", file=sys.stderr)
        return 2
    model_id = settings.model.inference_model
    if model_id is None:
        print("Refusing live smoke: configure a model provider and model ID.", file=sys.stderr)
        return 2
    run_id = args.sequence_id or datetime.now(UTC).strftime("minecraft-live-smoke-%Y%m%dT%H%M%SZ")
    database = _database_for(args, run_id)
    database.parent.mkdir(parents=True, exist_ok=True)
    trace = build_trace_sink(settings.trace, wandb=settings.wandb)
    summaries: list[dict[str, object]] = []
    timed_out = False
    budget_exhausted = False
    try:
        with EpisodeStore.open(database) as store:
            client = _BudgetedClient(
                build_model_client(settings.model, settings.wandb), store, run_id
            )
            executor = build_skill_executor(settings.sandbox)

            async def run_all() -> None:
                index = 1
                while True:
                    summary = await _run_sequence(
                        store=store,
                        client=client,
                        model_id=model_id,
                        executor=executor,
                        trace=trace,
                        sequence_id=f"{run_id}-s{index:02d}",
                        cycle_index=index,
                        settings=settings,
                    )
                    summaries.append(summary)
                    primitives_used = sum(
                        int(item["training"]["primitives_used"])
                        + (0 if item["reuse"] is None else int(item["reuse"]["primitives_used"]))
                        for item in summaries
                    )
                    if len(_records_for_run(store, run_id)) >= CALL_BUDGET or sum(
                        _call_tokens(record) for record in _records_for_run(store, run_id)
                    ) >= TOKEN_BUDGET or primitives_used >= PRIMITIVE_BUDGET:
                        return
                    index += 1

            try:
                asyncio.run(asyncio.wait_for(run_all(), timeout=DEADLINE_SECONDS))
            except TimeoutError:
                timed_out = True
            except SmokeBudgetExhausted:
                budget_exhausted = True
            records = _records_for_run(store, run_id)
            payload = {
                "run_kind": CONDITION,
                "sequence_id": run_id,
                "status": (
                    "deadline_exceeded"
                    if timed_out
                    else ("budget_exhausted" if budget_exhausted else "completed")
                ),
                "deadline_seconds": DEADLINE_SECONDS,
                "token_budget": TOKEN_BUDGET,
                "tokens_used": sum(_call_tokens(record) for record in records),
                "call_budget": CALL_BUDGET,
                "calls_used": len(records),
                "primitive_budget": PRIMITIVE_BUDGET,
                "cycle_decision_budget": SMOKE_CYCLE_DECISION_BUDGET,
                "cycle_primitive_budget": SMOKE_CYCLE_PRIMITIVE_BUDGET,
                "max_repairs": MAX_REPAIRS,
                "database": str(database),
                "weave_run_prefix": run_id,
                "sequences": summaries,
                "heldout_evaluation": False,
            }
    finally:
        with suppress(Exception):
            trace.flush()
    print(json.dumps(payload, indent=2))
    return 124 if timed_out else 0


if __name__ == "__main__":
    raise SystemExit(main())
