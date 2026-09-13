"""Run the non-benchmark Minecraft easy-mode live diagnostic on the local server.

The room is deliberately trivial: press the visible nearby button and the iron
bars open. It checks perception, action selection, JSON compliance, and
connector execution before the Resonator mechanic is attempted. It is a local
diagnostic only; nothing it produces is evaluation, held-out, or transfer
evidence.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
import sys
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, NamedTuple

from noob_agent.agents.action import ActionAgent
from noob_agent.agents.builder import BuilderAgent
from noob_agent.agents.evidence import select_evidence
from noob_agent.connectors.minecraft import MinecraftConnector, MinecraftSettings
from noob_agent.domain.model import ConnectorManifest, Observation, StepResult, ToolRequest
from noob_agent.domain.records import ExperimentRecord
from noob_agent.models.client import ModelClient, ModelRequest, ModelResponse, build_model_client
from noob_agent.models.recording import RecordingModelClient
from noob_agent.observability.tracing import build_trace_sink
from noob_agent.runtime.runner import EpisodeRunner
from noob_agent.settings import IntegrationSettings
from noob_agent.skills.executor import build_skill_executor
from noob_agent.skills.registry import SkillRegistry
from noob_agent.skills.runtime import SkillRuntime
from noob_agent.storage import EpisodeStore


def _load_live_smoke() -> ModuleType:
    """Reuse the Resonator smoke runner's chat transcript and token accounting."""
    name = "run_minecraft_live_smoke"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_live_smoke = _load_live_smoke()
MinecraftChatTranscriptClient = _live_smoke.MinecraftChatTranscriptClient
_call_tokens = _live_smoke._call_tokens
_records_for_run = _live_smoke._records_for_run

SCENARIO_ID = "easy-button-gate-v1"
CONDITION = "non-benchmark-minecraft-easy-diagnostic"
RESET_COMMANDS = ("function noob_agent_easy:reset",)
SEED = 20260913
SUCCESS_MESSAGE = "The iron-bar gateway opens."
SUCCESS_REASON = "gateway_open"
EASY_PUBLIC_GOAL = (
    "Open the iron-bar gateway in front of you. Press the stone button labelled "
    "Gate Button beside the iron bars with use_object. Success is visible: the "
    "iron bars disappear and the message 'The iron-bar gateway opens.' appears."
)

# Diagnostic-only generosity. None of these is a normal evaluation budget.
DEADLINE_SECONDS: float | None = None
# ExperimentRecord requires a positive wall-time budget; about 31 years is
# effectively none.
UNBOUNDED_WALL_TIME_MS = 10**12
ACTION_MAX_OUTPUT_TOKENS = 32_000
BUILDER_MAX_OUTPUT_TOKENS = 100_000
MAX_REPAIRS = 5
# Finite counts remain only so a broken model or connector cannot loop forever.
EPISODE_DECISION_BUDGET = 500
EPISODE_PRIMITIVE_BUDGET = 1_000
RUN_CALL_BUDGET = 2_000
RUN_TOKEN_BUDGET = 50_000_000


class RunOptions(NamedTuple):
    database: Path | None
    sequence_id: str | None
    live_easy_smoke: bool


class EasyBudgetExhausted(asyncio.CancelledError):
    """A new provider request would exceed the diagnostic run's call or token budget.

    A cancellation subclass, so `EpisodeRunner` durably finalizes an open episode.
    """


class EasyBudgetedClient:
    """Refuse to send a request once the whole run's generous budget is spent."""

    def __init__(
        self,
        inner: ModelClient,
        store: EpisodeStore,
        run_id: str,
        *,
        call_budget: int = RUN_CALL_BUDGET,
        token_budget: int = RUN_TOKEN_BUDGET,
    ) -> None:
        self._inner, self._store, self._run_id = inner, store, run_id
        self._call_budget, self._token_budget = call_budget, token_budget
        self.provider = getattr(inner, "provider", "unknown")

    async def complete(self, request: ModelRequest) -> ModelResponse:
        records = _records_for_run(self._store, self._run_id)
        if len(records) >= self._call_budget:
            raise EasyBudgetExhausted("Model-call budget exhausted; request was not sent.")
        if sum(_call_tokens(record) for record in records) >= self._token_budget:
            raise EasyBudgetExhausted("Token budget exhausted; request was not sent.")
        return await self._inner.complete(request)


class EasyGoalConnector:
    """Harness-only wrapper: state the easy goal and end on its public success.

    The manifest and every primitive pass through unchanged. Success is read
    only from the ordinary public message the room shows every nearby player;
    `announce` stays a harness operation and is never offered as a tool.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.episode_id: str | None = None

    def _public(self, observation: Observation) -> Observation:
        update: dict[str, object] = {"public_goal": EASY_PUBLIC_GOAL}
        if any(message.text == SUCCESS_MESSAGE for message in observation.messages):
            update.update(terminal=True, terminal_reason=SUCCESS_REASON)
        return observation.model_copy(update=update)

    async def manifest(self) -> ConnectorManifest:
        manifest: ConnectorManifest = await self._inner.manifest()
        return manifest

    async def reset(self, scenario_id: str, seed: int) -> Observation:
        observation = self._public(await self._inner.reset(scenario_id, seed))
        self.episode_id = observation.episode_id
        return observation

    async def step(self, request: ToolRequest) -> StepResult:
        result: StepResult = await self._inner.step(request)
        return result.model_copy(update={"observation": self._public(result.observation)})

    async def is_terminal(self) -> bool:
        terminal: bool = await self._inner.is_terminal()
        return terminal

    async def close(self) -> None:
        await self._inner.close()

    async def announce(self, text: str) -> None:
        await self._inner.announce(text)


def _easy_settings() -> MinecraftSettings:
    return MinecraftSettings(scenario_reset_commands=RESET_COMMANDS)


def _new_connector() -> MinecraftConnector:
    return MinecraftConnector(_easy_settings())


def _parse_args(argv: Sequence[str] | None = None) -> RunOptions:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=None)
    parser.add_argument("--sequence-id", default=None)
    parser.add_argument("--live-easy-smoke", action="store_true")
    parsed = parser.parse_args(argv)
    if not parsed.live_easy_smoke:
        parser.error(
            "--live-easy-smoke is required; this non-benchmark runner is not an evaluation"
        )
    return RunOptions(
        database=parsed.database, sequence_id=parsed.sequence_id, live_easy_smoke=True
    )


def _default_run_id(now: datetime) -> str:
    return now.strftime("non-benchmark-minecraft-easy-%Y%m%dT%H%M%SZ")


def _database_for(options: RunOptions, run_id: str) -> Path:
    return options.database or Path(f".noob-agent/{run_id}.sqlite3")


def _easy_experiment(
    *, experiment_id: str, model_id: str, connector_version: str, created_at: datetime
) -> ExperimentRecord:
    return ExperimentRecord(
        experiment_id=experiment_id,
        model_id=model_id,
        condition=CONDITION,
        connector_version=connector_version,
        decision_budget=EPISODE_DECISION_BUDGET,
        primitive_budget=EPISODE_PRIMITIVE_BUDGET,
        wall_time_budget_ms=UNBOUNDED_WALL_TIME_MS,
        created_at=created_at,
    )


def _training_summary(store: EpisodeStore, episode_id: str) -> dict[str, Any]:
    """Read one episode's durable outcome, including an interrupted one."""
    stored = store.read_episode(episode_id)
    outcome = stored.outcome
    terminal = outcome is not None and outcome.terminal
    return {
        "episode_id": episode_id,
        "stop_reason": None if outcome is None else outcome.stop_reason,
        "terminal_reason": SUCCESS_REASON if terminal else None,
        "decisions_used": 0 if outcome is None else outcome.total_decisions,
        "primitives_used": (
            sum(step.result.primitive_actions_charged for step in stored.steps)
            if outcome is None
            else outcome.total_primitives
        ),
    }


async def _run_easy(
    *,
    store: EpisodeStore,
    client: ModelClient,
    model_id: str,
    executor: Any,
    trace: Any,
    run_id: str,
    connector_factory: Callable[[], Any] = _new_connector,
    summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One cold attempt, one Builder pass, and a same-room reuse if accepted.

    `summary` is filled in place, so an interrupted run still reports the
    progress that was durably recorded before the interrupt.
    """
    summary = {} if summary is None else summary
    sequence_id = f"{run_id}-s01"
    summary.update(sequence_id=sequence_id, training=None, builder=None, reuse=None)
    connector = EasyGoalConnector(connector_factory())
    manifest = await connector.manifest()
    training = _easy_experiment(
        experiment_id=f"{sequence_id}-training",
        model_id=model_id,
        connector_version=manifest.connector_version,
        created_at=datetime.now(UTC),
    )
    store.create_experiment(training)
    registry = SkillRegistry()
    try:
        try:
            cold = await EpisodeRunner(
                connector=connector,
                store=store,
                policy=ActionAgent(
                    RecordingModelClient(
                        MinecraftChatTranscriptClient(client, connector, purpose="action"),
                        store=store,
                        experiment_id=training.experiment_id,
                        role="action",
                        model_id=model_id,
                        trace=trace,
                    ),
                    manifest=manifest,
                    max_output_tokens=ACTION_MAX_OUTPUT_TOKENS,
                ),
                trace=trace,
                # Keep the bot connected so Builder calls still narrate in chat.
                close_connector=False,
            ).run(experiment=training, scenario_id=SCENARIO_ID, seed=SEED, split="training")
        finally:
            if connector.episode_id is not None:
                with suppress(Exception):
                    summary["training"] = _training_summary(store, connector.episode_id)
        stored = store.read_episode(cold.episode_id)
        outcome = await BuilderAgent(
            RecordingModelClient(
                MinecraftChatTranscriptClient(client, connector, purpose="builder"),
                store=store,
                experiment_id=training.experiment_id,
                role="builder",
                model_id=model_id,
                episode_id=cold.episode_id,
                trace=trace,
            ),
            registry,
            executor=executor,
            max_repairs=MAX_REPAIRS,
            max_output_tokens=BUILDER_MAX_OUTPUT_TOKENS,
        ).build(
            select_evidence(stored),
            training_trace=stored,
            primitive_names=tuple(tool.name for tool in manifest.tools),
            authoring_model_id=model_id,
            created_at=datetime.now(UTC),
        )
    finally:
        # The first bot leaves before any reuse attempt joins as the same player.
        with suppress(Exception):
            await connector.close()
    summary["builder"] = {
        "accepted": outcome.accepted,
        "stop_reason": outcome.stop_reason,
        "attempts": outcome.attempts,
        "skill": (
            None if outcome.version is None else f"{outcome.version.name}@{outcome.version.version}"
        ),
    }
    if not outcome.accepted:
        return summary

    reuse_connector = EasyGoalConnector(connector_factory())
    reuse_manifest = await reuse_connector.manifest()
    reuse_experiment = _easy_experiment(
        experiment_id=f"{sequence_id}-reuse",
        model_id=model_id,
        connector_version=reuse_manifest.connector_version,
        created_at=datetime.now(UTC),
    )
    store.create_experiment(reuse_experiment)
    reuse = await EpisodeRunner(
        connector=reuse_connector,
        store=store,
        policy=ActionAgent(
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
            max_output_tokens=ACTION_MAX_OUTPUT_TOKENS,
        ),
        trace=trace,
        skills=SkillRuntime(executor, available=registry.available_skills()),
    ).run(
        experiment=reuse_experiment,
        scenario_id=SCENARIO_ID,
        # A fresh connector restarts reset counters; a new seed keeps episode IDs unique.
        seed=SEED + 1,
        split="training",
    )
    summary["reuse"] = {
        "episode_id": reuse.episode_id,
        "stop_reason": reuse.stop_reason,
        "decisions_used": reuse.decisions_used,
        "primitives_used": reuse.primitives_used,
        "skill_uses": len(reuse.skill_uses),
    }
    return summary


def _drive(run: Callable[[], Awaitable[None]]) -> str:
    """Run to completion; Ctrl-C is the user-visible interrupt path."""

    async def main() -> None:
        await run()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        return "interrupted"
    except EasyBudgetExhausted:
        return "budget_exhausted"
    return "completed"


def _summary_lines(payload: dict[str, Any]) -> list[str]:
    sequence = payload.get("sequence") or {}
    training = sequence.get("training") or {}
    builder = sequence.get("builder") or {}
    reuse = sequence.get("reuse")
    lines = [
        f"[{payload['run_kind']}] local diagnostic only; not an evaluation result",
        f"scenario:     {payload['scenario_id']}",
        f"run status:   {payload['status']}",
        f"stop reason:  {training.get('stop_reason', 'not reached')}"
        + (" (gateway opened)" if training.get("terminal_reason") == SUCCESS_REASON else ""),
        f"decisions:    {training.get('decisions_used', 0)}",
        f"primitives:   {training.get('primitives_used', 0)}",
        f"model calls:  {payload['calls_used']}",
        f"tokens:       {payload['tokens_used']}",
        f"skill:        {builder.get('skill') or 'none accepted'}",
    ]
    if reuse is not None:
        lines.append(
            f"reuse:        {reuse['stop_reason']}, {reuse['decisions_used']} decisions, "
            f"{reuse['primitives_used']} primitives, {reuse['skill_uses']} skill uses"
        )
    lines.append(f"database:     {payload['database']}")
    return lines


def main(argv: Sequence[str] | None = None, *, environ: dict[str, str] | None = None) -> int:
    args = _parse_args(argv)
    settings = IntegrationSettings.from_environ(os.environ if environ is None else environ)
    if not settings.trace.enabled:
        print("Refusing easy diagnostic: Weave tracing must be enabled.", file=sys.stderr)
        return 2
    model_id = settings.model.inference_model
    if model_id is None:
        print("Refusing easy diagnostic: configure a model provider and model ID.", file=sys.stderr)
        return 2
    run_id = args.sequence_id or _default_run_id(datetime.now(UTC))
    database = _database_for(args, run_id)
    database.parent.mkdir(parents=True, exist_ok=True)
    trace = build_trace_sink(settings.trace, wandb=settings.wandb)
    result: dict[str, Any] = {}
    try:
        with EpisodeStore.open(database) as store:
            client = EasyBudgetedClient(
                build_model_client(settings.model, settings.wandb), store, run_id
            )
            executor = build_skill_executor(settings.sandbox)

            async def run() -> None:
                await _run_easy(
                    store=store,
                    client=client,
                    model_id=model_id,
                    executor=executor,
                    trace=trace,
                    run_id=run_id,
                    summary=result,
                )

            status = _drive(run)
            records = _records_for_run(store, run_id)
            payload = {
                "run_kind": CONDITION,
                "scenario_id": SCENARIO_ID,
                "status": status,
                "database": str(database),
                "calls_used": len(records),
                "tokens_used": sum(_call_tokens(record) for record in records),
                "sequence": result,
            }
    finally:
        with suppress(Exception):
            trace.flush()
    print("\n".join(_summary_lines(payload)))
    return 130 if status == "interrupted" else 0


if __name__ == "__main__":
    raise SystemExit(main())
