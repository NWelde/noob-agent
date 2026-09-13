"""The Action agent: one decision per turn from public information only.

On each step the agent receives the public goal, the current observation, a
bounded recent history of its own decisions, the primitive tools, and the
accepted skills it may invoke. It chooses exactly one tool or skill and states
its subgoal and the evidence it expects, per `hackathon_plan.md` section 6.3.

The agent's conversation lives only inside this object. A fresh agent, or
`reset()`, starts with nothing: no training trace, no Builder discussion, no
prior episode. That is what the held-out reset relies on.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from noob_agent.domain.findings import FindingReport, parse_finding_report
from noob_agent.domain.model import ConnectorManifest, Observation, StepResult, ToolRequest
from noob_agent.domain.skills import SkillVersion
from noob_agent.models.client import ModelClient, ModelRequest
from noob_agent.prompts.action import ACTION_SYSTEM, HistoryEntry, render_action_prompt
from noob_agent.skills.runtime import SkillInvocation, SkillRequest

DEFAULT_HISTORY_LIMIT = 6
DEFAULT_MAX_OUTPUT_TOKENS = 512

# The tool name an unusable reply is sent under; no manifest declares it, so
# the connector refuses it, which costs a decision and no primitive.
UNUSABLE_REPLY_TOOL = "unusable_reply"

_JSON_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)

DecisionKind = Literal["primitive", "skill", "unusable"]


class ParsedDecision(BaseModel):
    """What one reply asked for, before anything judged whether it exists."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: DecisionKind
    name: str = ""
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    subgoal: str = ""
    expected_evidence: str = ""
    # A candidate defect the model attached to this decision, if it did.
    finding: FindingReport | None = None


class Decision(BaseModel):
    """One issued decision: the request sent plus the agent's stated intent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: str = Field(min_length=1)
    kind: DecisionKind
    name: str = Field(min_length=1)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    subgoal: str
    expected_evidence: str
    finding: FindingReport | None = None


def _first_json_object(reply: str) -> dict[str, JsonValue] | None:
    fenced = _JSON_FENCE.search(reply)
    candidates = [fenced.group(1)] if fenced else []
    start = reply.find("{")
    if start != -1:
        candidates.append(reply[start:])
    decoder = json.JSONDecoder()
    for candidate in candidates:
        # A trailing remark after the object is common; take the object only.
        try:
            parsed: object = decoder.raw_decode(candidate.strip())[0]
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return {str(key): value for key, value in parsed.items()}
    return None


def parse_decision(reply: str) -> ParsedDecision:
    """Read one decision out of a reply, fenced or bare; unusable when absent."""
    parsed = _first_json_object(reply)
    if parsed is None:
        return ParsedDecision(kind="unusable")

    subgoal = parsed.get("subgoal")
    expected = parsed.get("expected_evidence")
    stated_subgoal = subgoal if isinstance(subgoal, str) else ""
    stated_evidence = expected if isinstance(expected, str) else ""
    finding = parse_finding_report(parsed.get("finding"))

    skill = parsed.get("skill")
    if isinstance(skill, str) and skill:
        inputs = parsed.get("inputs")
        return ParsedDecision(
            kind="skill",
            name=skill,
            arguments=inputs if isinstance(inputs, dict) else {},
            subgoal=stated_subgoal,
            expected_evidence=stated_evidence,
            finding=finding,
        )
    tool = parsed.get("tool")
    if isinstance(tool, str) and tool:
        arguments = parsed.get("arguments")
        return ParsedDecision(
            kind="primitive",
            name=tool,
            arguments=arguments if isinstance(arguments, dict) else {},
            subgoal=stated_subgoal,
            expected_evidence=stated_evidence,
            finding=finding,
        )
    return ParsedDecision(
        kind="unusable",
        subgoal=stated_subgoal,
        expected_evidence=stated_evidence,
        finding=finding,
    )


class ActionAgent:
    """Chooses one primitive or skill per turn through a model client."""

    def __init__(
        self,
        client: ModelClient,
        *,
        manifest: ConnectorManifest,
        skills: Sequence[SkillVersion] = (),
        history_limit: int = DEFAULT_HISTORY_LIMIT,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    ) -> None:
        for skill in skills:
            if skill.status != "accepted":
                raise ValueError(
                    f"Skill {skill.name!r} version {skill.version} is {skill.status!r}; "
                    "only accepted skills may be offered to the Action agent."
                )
        if history_limit < 0:
            raise ValueError("history_limit cannot be negative.")
        self._client = client
        self._manifest = manifest
        self._skills = tuple(skills)
        self._history_limit = history_limit
        self._max_output_tokens = max_output_tokens
        self._history: list[HistoryEntry] = []
        self._decisions: list[Decision] = []
        self._pending: dict[str, Decision] = {}
        self._issued = 0
        self._input_tokens = 0
        self._output_tokens = 0

    def reset(self) -> None:
        """Forget this conversation entirely."""
        self._history.clear()
        self._decisions.clear()
        self._pending.clear()
        self._issued = 0
        self._input_tokens = 0
        self._output_tokens = 0

    @property
    def decisions(self) -> tuple[Decision, ...]:
        return tuple(self._decisions)

    @property
    def input_tokens(self) -> int:
        return self._input_tokens

    @property
    def output_tokens(self) -> int:
        return self._output_tokens

    async def choose(self, observation: Observation) -> ToolRequest | SkillRequest:
        """Ask the model for one decision and turn it into one request."""
        prompt = render_action_prompt(
            public_goal=observation.public_goal,
            observation=observation,
            tools=self._manifest.tools,
            skills=self._skills,
            history=self._history[-self._history_limit :] if self._history_limit else (),
        )
        response = await self._client.complete(
            ModelRequest(
                system=ACTION_SYSTEM, prompt=prompt, max_output_tokens=self._max_output_tokens
            )
        )
        self._input_tokens += response.input_tokens
        self._output_tokens += response.output_tokens

        parsed = parse_decision(response.text)
        self._issued += 1
        action_id = f"a_{self._issued:04d}"

        request: ToolRequest | SkillRequest
        if parsed.kind == "skill":
            request = SkillRequest(
                action_id=action_id, skill_name=parsed.name, inputs=parsed.arguments
            )
        elif parsed.kind == "primitive":
            request = ToolRequest(
                action_id=action_id, tool_name=parsed.name, arguments=parsed.arguments
            )
        else:
            request = ToolRequest(action_id=action_id, tool_name=UNUSABLE_REPLY_TOOL)

        decision = Decision(
            action_id=action_id,
            kind=parsed.kind,
            name=parsed.name or UNUSABLE_REPLY_TOOL,
            arguments=parsed.arguments,
            subgoal=parsed.subgoal,
            expected_evidence=parsed.expected_evidence,
            finding=parsed.finding,
        )
        self._decisions.append(decision)
        self._pending[action_id] = decision
        return request

    def notice(
        self, request: ToolRequest | SkillRequest, outcome: StepResult | SkillInvocation
    ) -> None:
        """Fold a decision's public outcome into the bounded history."""
        decision = self._pending.pop(request.action_id, None)
        subgoal = decision.subgoal if decision is not None else ""

        if isinstance(request, SkillRequest):
            chosen = f"skill {request.skill_name}({json.dumps(request.inputs, sort_keys=True)})"
        else:
            chosen = f"{request.tool_name}({json.dumps(request.arguments, sort_keys=True)})"

        if isinstance(outcome, SkillInvocation):
            result = outcome.use.result
            detail = result.summary if result is not None else outcome.use.message
            summary = (
                f"skill {outcome.agent_status}: {detail} "
                f"[used {outcome.use.primitive_actions_consumed} primitive actions]"
            )
        else:
            summary = (
                f"{outcome.status}/{outcome.code}: {outcome.message} "
                f"[charged {outcome.primitive_actions_charged}]"
            )

        self._history.append(
            HistoryEntry(
                action_id=request.action_id, subgoal=subgoal, chosen=chosen, outcome=summary
            )
        )
