"""The headless loop bench: fixed-seed sequences in, a scorecard out."""

from __future__ import annotations

import importlib.util
import itertools
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest
from fakes.connector import ScriptedConnector, ScriptedStep

from noob_agent.domain.records import StoredEpisode
from noob_agent.models.client import ModelRequest, ModelResponse
from noob_agent.prompts.builder import BUILDER_SYSTEM
from noob_agent.skills.executor import LocalSubprocessSkillExecutor

SKILL_SOURCE = '''"""Record one fresh public observation."""

from noob_agent.skills.contract import EvidenceRef, SkillContext, SkillResult


async def run(context: SkillContext, inputs: dict[str, object]) -> SkillResult:
    """Record one fresh public observation without changing the environment."""
    if inputs:
        return SkillResult(status="failed", summary="Unexpected input.", primitive_actions_used=0)
    observation = await context.observe()
    return SkillResult(
        status="inconclusive",
        summary="Recorded the current public state.",
        evidence=(EvidenceRef(kind="observation_sequence", value=str(observation.sequence)),),
        outputs={"terminal": observation.terminal},
        primitive_actions_used=0,
    )
'''

METADATA = {
    "name": "record_visible_state",
    "version": 1,
    "parent_version": None,
    "purpose": "Record one fresh public observation without changing the environment.",
    "input_schema": {"properties": {}},
    "required_tools": ["observe"],
    "max_primitive_actions": 1,
    "max_wall_time_seconds": 5,
    "success_claim": "The result cites the newly observed public sequence.",
    "api_version": "noob-agent.skill.v1",
}
CANDIDATE = f"```python\n{SKILL_SOURCE}```\n\n```json\n{json.dumps(METADATA)}\n```\n"


def _module() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "loop_bench.py"
    spec = importlib.util.spec_from_file_location("loop_bench", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ScriptedClient:
    provider = "scripted"

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if request.system == BUILDER_SYSTEM:
            text = CANDIDATE
        else:
            text = json.dumps(
                {
                    "subgoal": "Look.",
                    "expected_evidence": "State.",
                    "tool": "observe",
                    "arguments": {},
                }
            )
        return ModelResponse(text=text, input_tokens=100, output_tokens=20, model_id="fake-model")


@dataclass(frozen=True)
class Grade:
    goal_completed: bool


def _factory() -> ScriptedConnector:
    return ScriptedConnector(
        ScriptedStep(),
        ScriptedStep(terminal=True, terminal_reason="done"),
        episode_id=f"ep_{next(_ids):05d}",
    )


_ids = itertools.count(1)


def _grade(stored: StoredEpisode, connector: ScriptedConnector) -> Grade:
    del connector
    return Grade(goal_completed=stored.outcome is not None and stored.outcome.terminal)


@pytest.fixture
def manifest(tmp_path: Path) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "scenarios": {
                    "fake-training": {"split": "training", "seeds": [7]},
                    "fake-heldout": {"split": "held-out", "seeds": [11, 12]},
                }
            }
        ),
        encoding="utf-8",
    )
    return path


ENVIRONMENT = {
    "NOOB_AGENT_MODEL_PROVIDER": "wandb-inference",
    "NOOB_AGENT_INFERENCE_MODEL": "fake-model",
    "WANDB_API_KEY": "not-a-real-key",
    "NOOB_AGENT_SANDBOX_MODE": "local",
}


def _dependencies(module: ModuleType) -> object:
    return module.BenchDependencies(
        connector_factory=_factory,
        client=ScriptedClient(),
        grade=_grade,
        executor=LocalSubprocessSkillExecutor(),
    )


def test_run_refuses_a_disabled_provider(tmp_path: Path, manifest: Path) -> None:
    code = _module().main(
        ["run", "--output-dir", str(tmp_path), "--manifest", str(manifest)], environ={}
    )
    assert code == 2


def test_run_refuses_a_disabled_sandbox(tmp_path: Path, manifest: Path) -> None:
    environment = {**ENVIRONMENT, "NOOB_AGENT_SANDBOX_MODE": "disabled"}
    code = _module().main(
        ["run", "--output-dir", str(tmp_path), "--manifest", str(manifest)],
        environ=environment,
    )
    assert code == 2


def test_run_never_reuses_an_existing_database(tmp_path: Path, manifest: Path) -> None:
    module = _module()
    (tmp_path / "taken.sqlite3").write_bytes(b"recorded run")

    code = module.main(
        [
            "run",
            "--output-dir",
            str(tmp_path),
            "--manifest",
            str(manifest),
            "--run-id",
            "taken",
        ],
        environ=ENVIRONMENT,
        dependencies=_dependencies(module),
    )

    assert code == 2
    assert (tmp_path / "taken.sqlite3").read_bytes() == b"recorded run"


def test_run_writes_a_scorecard_for_every_fixed_seed_sequence(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _module()

    code = module.main(
        [
            "run",
            "--sequences",
            "2",
            "--output-dir",
            str(tmp_path),
            "--manifest",
            str(manifest),
            "--run-id",
            "bench-test",
        ],
        environ=ENVIRONMENT,
        dependencies=_dependencies(module),
    )

    assert code == 0
    scorecard = json.loads((tmp_path / "bench-test.json").read_text(encoding="utf-8"))
    sequences = scorecard["sequences"]
    assert [sequence["sequence_id"] for sequence in sequences] == [
        "bench-test-s01",
        "bench-test-s02",
    ]
    assert all(sequence["acceptance_source"] == "result" for sequence in sequences)
    assert all(sequence["builder"]["accepted"] is True for sequence in sequences)
    assert all(sequence["training"]["seed"] == 7 for sequence in sequences)
    assert all(len(sequence["heldout"]) == 2 for sequence in sequences)
    assert scorecard["totals"]["sequences"] == 2
    assert (tmp_path / "bench-test.sqlite3").exists()
    assert (tmp_path / "bench-test.md").read_text(encoding="utf-8").startswith("# ")
    assert "bench-test" in capsys.readouterr().out


def test_score_reads_an_existing_database_without_changing_it(
    tmp_path: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _module()
    module.main(
        [
            "run",
            "--output-dir",
            str(tmp_path),
            "--manifest",
            str(manifest),
            "--run-id",
            "recorded",
        ],
        environ=ENVIRONMENT,
        dependencies=_dependencies(module),
    )
    database = tmp_path / "recorded.sqlite3"
    before = database.read_bytes()
    capsys.readouterr()

    code = module.main(
        ["score", "--database", str(database), "--output-dir", str(tmp_path / "scores")],
        environ={},
    )

    assert code == 0
    assert database.read_bytes() == before
    written = json.loads((tmp_path / "scores" / "recorded.json").read_text(encoding="utf-8"))
    assert written["sequences"][0]["acceptance_source"] == "inferred"
    assert "recorded-s01" in capsys.readouterr().out


def test_run_with_rounds_uses_practice_seeds_and_reports_each_round(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _module()
    manifest = tmp_path / "manifest-v2.json"
    manifest.write_text(
        json.dumps(
            {
                "scenarios": {
                    "fake-training": {"split": "training", "seeds": [7], "practice_seeds": [8, 9]},
                    "fake-heldout": {"split": "held-out", "seeds": [11]},
                }
            }
        ),
        encoding="utf-8",
    )

    code = module.main(
        [
            "run",
            "--sequences",
            "1",
            "--rounds",
            "1",
            "--curve",
            "--output-dir",
            str(tmp_path),
            "--manifest",
            str(manifest),
            "--run-id",
            "rounds-test",
        ],
        environ=ENVIRONMENT,
        dependencies=_dependencies(module),
    )

    assert code == 0
    (sequence,) = json.loads((tmp_path / "rounds-test.json").read_text(encoding="utf-8"))[
        "sequences"
    ]
    assert sequence["condition"] == "multi-round"
    # The scripted game ends every practice episode, so the first skill is already perfect.
    assert [r["decision"] for r in sequence["rounds"]] == ["incumbent"]
    assert sequence["rounds"][0]["practice_rate"] == 1.0
    assert sequence["loop_stop_reason"] == "perfect_practice"
    assert len(sequence["practice"]) == 2
    assert [(p["version"], p["heldout_episodes"]) for p in sequence["curve"]] == [(1, 1)]
    assert "| Round |" in capsys.readouterr().out


def test_rounds_require_practice_seeds_in_the_manifest(tmp_path: Path, manifest: Path) -> None:
    module = _module()

    code = module.main(
        ["run", "--rounds", "1", "--output-dir", str(tmp_path), "--manifest", str(manifest)],
        environ=ENVIRONMENT,
        dependencies=_dependencies(module),
    )

    assert code == 2
