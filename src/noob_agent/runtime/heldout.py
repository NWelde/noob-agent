"""The held-out evaluation path: fresh world, fresh conversation, accepted skill.

Before a held-out episode, the game and the model conversation reset. The
Action agent receives the public task, the primitive manifest, and the accepted
skill's name, purpose, input schema, and result shape, and nothing else
(`skill_contract.md`, Held-out rules). A held-out failure never triggers a
repair: this module has no path to the Builder or to validation, and it never
changes the registry.

Held-out budgets are the ones frozen in `connector_contract.md`. An experiment
asking for more is refused before the world is touched.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from noob_agent.agents.action import (
    DEFAULT_HISTORY_LIMIT,
    DEFAULT_MAX_OUTPUT_TOKENS,
    ActionAgent,
    Decision,
)
from noob_agent.connectors import GameConnector
from noob_agent.domain.records import ExperimentRecord
from noob_agent.domain.skills import SkillVersion
from noob_agent.models.client import ModelClient
from noob_agent.observability.tracing import TraceSink
from noob_agent.runtime.runner import Clock, EpisodeResult, EpisodeRunner
from noob_agent.skills.executor import SkillExecutor
from noob_agent.skills.registry import SkillRegistry
from noob_agent.skills.runtime import SkillRuntime, SkillUseRecord
from noob_agent.storage import EpisodeStore

# The held-out attempt budgets from connector_contract.md.
HELD_OUT_DECISION_BUDGET = 12
HELD_OUT_PRIMITIVE_BUDGET = 24
HELD_OUT_WALL_TIME_MS = 90_000


def heldout_experiment(
    *,
    experiment_id: str,
    model_id: str,
    connector_version: str,
    created_at: datetime,
    condition: str = "self-improving",
) -> ExperimentRecord:
    """An experiment record carrying exactly the frozen held-out budgets."""
    return ExperimentRecord(
        experiment_id=experiment_id,
        model_id=model_id,
        condition=condition,
        connector_version=connector_version,
        decision_budget=HELD_OUT_DECISION_BUDGET,
        primitive_budget=HELD_OUT_PRIMITIVE_BUDGET,
        wall_time_budget_ms=HELD_OUT_WALL_TIME_MS,
        created_at=created_at,
    )


def check_heldout_budgets(experiment: ExperimentRecord) -> None:
    """Refuse an experiment whose budgets exceed the frozen held-out limits."""
    if experiment.decision_budget > HELD_OUT_DECISION_BUDGET:
        raise ValueError(
            f"Held-out decision budget is {HELD_OUT_DECISION_BUDGET}; "
            f"experiment asks for {experiment.decision_budget}."
        )
    if experiment.primitive_budget > HELD_OUT_PRIMITIVE_BUDGET:
        raise ValueError(
            f"Held-out primitive budget is {HELD_OUT_PRIMITIVE_BUDGET}; "
            f"experiment asks for {experiment.primitive_budget}."
        )
    if experiment.wall_time_budget_ms > HELD_OUT_WALL_TIME_MS:
        raise ValueError(
            f"Held-out wall-time budget is {HELD_OUT_WALL_TIME_MS} ms; "
            f"experiment asks for {experiment.wall_time_budget_ms}."
        )


@dataclass(frozen=True)
class HeldOutResult:
    """One held-out episode: its outcome, what was offered, and what was used."""

    episode: EpisodeResult
    offered_skills: tuple[SkillVersion, ...]
    skill_uses: tuple[SkillUseRecord, ...]
    decisions: tuple[Decision, ...]
    input_tokens: int
    output_tokens: int


class HeldOutRunner:
    """Runs one held-out episode with a fresh Action agent and the accepted skills."""

    def __init__(
        self,
        *,
        connector: GameConnector,
        store: EpisodeStore,
        registry: SkillRegistry,
        client: ModelClient,
        executor: SkillExecutor,
        clock: Clock | None = None,
        trace: TraceSink | None = None,
        history_limit: int = DEFAULT_HISTORY_LIMIT,
        action_max_output_tokens: int | None = None,
        action_thinking: bool | None = None,
    ) -> None:
        self._connector = connector
        self._store = store
        self._registry = registry
        self._client = client
        self._executor = executor
        self._clock = clock
        self._trace = trace
        self._history_limit = history_limit
        self._action_max_output_tokens = action_max_output_tokens
        self._action_thinking = action_thinking

    async def run(
        self, *, experiment: ExperimentRecord, scenario_id: str, seed: int
    ) -> HeldOutResult:
        """Reset the world and the conversation, then attempt the held-out scenario."""
        check_heldout_budgets(experiment)

        manifest = await self._connector.manifest()
        # Only accepted versions are offered; the registry exposes nothing else.
        offered = self._registry.available_skills()

        # A new agent per episode is the conversation reset: it starts with no
        # history, no training trace, and no prior episode.
        agent = ActionAgent(
            self._client,
            manifest=manifest,
            skills=offered,
            history_limit=self._history_limit,
            max_output_tokens=(
                DEFAULT_MAX_OUTPUT_TOKENS
                if self._action_max_output_tokens is None
                else self._action_max_output_tokens
            ),
            thinking=self._action_thinking,
        )
        runtime = SkillRuntime(self._executor, available=offered)
        runner = EpisodeRunner(
            connector=self._connector,
            store=self._store,
            policy=agent,
            clock=self._clock,
            trace=self._trace,
            skills=runtime,
        )
        episode = await runner.run(
            experiment=experiment, scenario_id=scenario_id, seed=seed, split="held-out"
        )
        return HeldOutResult(
            episode=episode,
            offered_skills=offered,
            skill_uses=episode.skill_uses,
            decisions=agent.decisions,
            input_tokens=agent.input_tokens,
            output_tokens=agent.output_tokens,
        )
