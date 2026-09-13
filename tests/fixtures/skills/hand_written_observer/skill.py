"""Hand-written plumbing fixture; never represent this as model-generated."""

from noob_agent.skills.contract import EvidenceRef, SkillContext, SkillResult


async def run(context: SkillContext, inputs: dict[str, object]) -> SkillResult:
    """Record one fresh public observation without changing the environment."""
    observation = await context.observe()
    return SkillResult(
        status="inconclusive",
        summary="Recorded the current public state without attempting an action.",
        evidence=(
            EvidenceRef(kind="observation_sequence", value=str(observation.sequence)),
        ),
        outputs={"terminal": observation.terminal},
        primitive_actions_used=0,
    )
