"""Step 23b: a local view of the run's reasoning, read from Weave as it arrives."""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

RUN = "doom-live-demo-20260913T120000Z"
EPISODE = "doom-basic-training-0000000000000001"
OTHER_EPISODE = "doom-basic-training-0000000000000099"
STARTED = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
PROMPT_MARK = "PROMPT-TEXT-MUST-NOT-BE-SERVED"
SYSTEM_MARK = "SYSTEM-TEXT-MUST-NOT-BE-SERVED"


def _module() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "live_reasoning_view.py"
    spec = importlib.util.spec_from_file_location("live_reasoning_view", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _call(
    call_id: str,
    op: str,
    seconds: int,
    inputs: dict[str, Any],
    output: dict[str, Any] | None = None,
    parent_id: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        parent_id=parent_id,
        op_name=f"weave:///entity/project/op/noob_agent.{op}:digest",
        started_at=STARTED + timedelta(seconds=seconds),
        inputs=inputs,
        output=output,
        ui_url=f"https://wandb.ai/entity/project/r/call/{call_id}",
    )


def _episode(
    call_id: str = "c-ep",
    *,
    experiment: str = f"{RUN}-training",
    episode: str = EPISODE,
    output: dict[str, Any] | None = None,
) -> SimpleNamespace:
    return _call(
        call_id,
        "episode",
        0,
        {
            "episode_id": episode,
            "experiment_id": experiment,
            "scenario_id": "doom-basic-training",
            "seed": 20260912,
            "split": "training",
            "public_goal": "Eliminate the hostile target in this area.",
        },
        output,
    )


def _action(
    call_id: str = "c-act",
    seconds: int = 1,
    *,
    episode: str = EPISODE,
    experiment: str = f"{RUN}-training",
) -> SimpleNamespace:
    reply = {
        "subgoal": "Center the Cacodemon.",
        "expected_evidence": "Its screen offset moves toward zero.",
        "action": "turn_right",
        "arguments": {"degrees": 10},
        "finding": None,
    }
    return _call(
        call_id,
        "model_call",
        seconds,
        {
            "purpose": "action",
            "episode_id": episode,
            "experiment_id": experiment,
            "action_id": "a_0001",
            "system": SYSTEM_MARK,
            "prompt": PROMPT_MARK,
            "response_text": json.dumps(reply),
            "reasoning": "The target is right of center.",
            "finish_reason": "stop",
            "input_tokens": 1200,
            "output_tokens": 60,
            "latency_ms": 540,
        },
        parent_id="c-ep",
    )


def _step(call_id: str = "c-step", seconds: int = 2, *, episode: str = EPISODE) -> SimpleNamespace:
    return _call(
        call_id,
        "step",
        seconds,
        {
            "episode_id": episode,
            "sequence": 1,
            "action_id": "a_0001",
            "tool_name": "turn_right",
            "arguments": {"degrees": 10},
            "status": "succeeded",
            "code": "OK",
            "message": "Action completed.",
            "terminal": False,
        },
        parent_id="c-ep",
    )


def _build(call_id: str = "c-build", seconds: int = 30) -> SimpleNamespace:
    return _call(
        call_id,
        "model_call",
        seconds,
        {
            "purpose": "build",
            "episode_id": EPISODE,
            "experiment_id": f"{RUN}-training",
            "system": SYSTEM_MARK,
            "prompt": PROMPT_MARK,
            "response_text": "Here is the skill.\n```python\nasync def run(context, inputs):\n"
            '    return None\n```\n```json\n{"name": "center_and_fire"}\n```',
            "reasoning": None,
            "finish_reason": "stop",
            "input_tokens": 3000,
            "output_tokens": 700,
            "latency_ms": 4200,
        },
    )


def _field(call: SimpleNamespace, path: str) -> Any:
    if path == "started_at":
        return str(call.started_at)
    return call.inputs.get(path.removeprefix("inputs."))


def _matches(call: SimpleNamespace, expression: dict[str, Any]) -> bool:
    """A small evaluator for the query shapes the view sends, as Weave would apply them."""
    ((operator, operand),) = expression.items()
    if operator == "$and":
        return all(_matches(call, part) for part in operand)
    if operator == "$or":
        return any(_matches(call, part) for part in operand)
    if operator == "$contains":
        value = _field(call, operand["input"]["$getField"])
        return isinstance(value, str) and operand["substr"]["$literal"] in value
    if operator == "$in":
        value = _field(call, operand[0]["$getField"])
        return value in [item["$literal"] for item in operand[1]]
    if operator == "$gte":
        value = _field(call, operand[0]["$getField"])
        return value is not None and value >= operand[1]["$literal"]
    if operator == "$eq":
        return _field(call, operand[0]["$getField"]) == operand[1]["$literal"]
    raise AssertionError(f"unexpected query operator {operator}")


class FakeWeave:
    """Applies the view's queries to the calls it holds, as the Weave server would.

    Calls in `hidden_until_finished` are not queryable until they have an output,
    which is how an open episode call behaved in a live run.
    """

    def __init__(self, *calls: SimpleNamespace) -> None:
        self.calls = list(calls)
        self.fail = False
        self.queries = 0
        self.hidden_until_finished: set[str] = set()

    def get_calls(self, **kwargs: Any) -> list[SimpleNamespace]:
        self.queries += 1
        if self.fail:
            raise ConnectionError("weave unreachable")
        visible = [
            call for call in self.calls if call.id not in self.hidden_until_finished or call.output
        ]
        if (kwargs.get("filter") or {}).get("trace_roots_only"):
            visible = [call for call in visible if call.parent_id is None]
        query = kwargs.get("query")
        chosen = [c for c in visible if query is None or _matches(c, query["$expr"])]
        return sorted(chosen, key=lambda call: call.started_at)


def test_events_are_built_from_episode_decision_step_and_builder_calls() -> None:
    module = _module()
    feed = module.WeaveFeed(FakeWeave(_episode(), _action(), _step(), _build()), run_id=RUN)

    events = feed.poll()

    kinds = [event["kind"] for event in events]
    assert kinds == ["episode", "decision", "result", "builder"]
    episode, decision, result, builder = events
    assert (episode["scenario_id"], episode["split"], episode["seed"]) == (
        "doom-basic-training",
        "training",
        20260912,
    )
    assert decision["subgoal"] == "Center the Cacodemon."
    assert decision["expected_evidence"] == "Its screen offset moves toward zero."
    assert (decision["action"], decision["arguments"]) == ("turn_right", {"degrees": 10})
    assert decision["reasoning"] == "The target is right of center."
    assert (decision["latency_ms"], decision["output_tokens"]) == (540, 60)
    assert decision["url"].endswith("/c-act")
    assert (result["action_id"], result["status"], result["code"]) == ("a_0001", "succeeded", "OK")
    assert builder["purpose"] == "build"
    assert builder["skill_name"] == "center_and_fire"
    assert "async def run" in builder["code"]


def test_calls_from_other_runs_are_excluded() -> None:
    module = _module()
    weave = FakeWeave(
        _episode(),
        _episode("c-other-ep", experiment="another-run-training", episode=OTHER_EPISODE),
        _action("c-other-act", experiment="another-run-training", episode=OTHER_EPISODE),
        _step("c-other-step", episode=OTHER_EPISODE),
        _action(),
    )

    events = module.WeaveFeed(weave, run_id=RUN).poll()

    assert [event["call_id"] for event in events] == ["c-ep", "c-act"]


def test_steps_are_fetched_even_when_a_later_builder_call_arrived_first() -> None:
    """Steps carry only an episode ID, so they must not fall behind the run's time cursor."""
    module = _module()
    weave = FakeWeave(_episode(), _action(), _step(), _build(seconds=40))
    feed = module.WeaveFeed(weave, run_id=RUN)

    feed.poll()
    feed.poll()

    kinds = [event["kind"] for event in feed.events]
    assert kinds.count("result") == 1
    assert kinds.count("decision") == 1 and kinds.count("builder") == 1


def test_repeated_polls_add_only_new_calls() -> None:
    module = _module()
    weave = FakeWeave(_episode(), _action())
    feed = module.WeaveFeed(weave, run_id=RUN)

    first = feed.poll()
    weave.calls.append(_step())
    second = feed.poll()
    third = feed.poll()

    assert [event["call_id"] for event in first] == ["c-ep", "c-act"]
    assert [event["call_id"] for event in second] == ["c-step"]
    assert third == []
    assert [event["index"] for event in feed.events] == [0, 1, 2]


def test_a_step_seen_before_its_episode_is_kept_until_the_episode_arrives() -> None:
    module = _module()
    weave = FakeWeave(_step())
    feed = module.WeaveFeed(weave, run_id=RUN)

    assert feed.poll() == []
    weave.calls.append(_episode())

    assert [event["kind"] for event in feed.poll()] == ["episode", "result"]


def test_a_finished_episode_reports_its_outcome_once() -> None:
    module = _module()
    open_episode = _episode()
    weave = FakeWeave(open_episode)
    feed = module.WeaveFeed(weave, run_id=RUN)
    feed.poll()

    open_episode.output = {"stop_reason": "terminal_state", "total_decisions": 7}
    finished = feed.poll()
    again = feed.poll()

    assert [(event["kind"], event["stop_reason"]) for event in finished] == [
        ("episode_finished", "terminal_state")
    ]
    assert again == []


def test_an_episode_visible_only_after_it_finishes_still_reports_start_and_outcome() -> None:
    module = _module()
    episode = _episode()
    weave = FakeWeave(episode, _action(), _step(), _build(seconds=40))
    weave.hidden_until_finished.add("c-ep")
    feed = module.WeaveFeed(weave, run_id=RUN)

    feed.poll()
    episode.output = {"stop_reason": "decision_limit", "total_decisions": 20}
    feed.poll()
    feed.poll()

    kinds = [event["kind"] for event in feed.events]
    assert kinds.count("episode") == 1 and kinds.count("episode_finished") == 1
    assert kinds.index("episode") < kinds.index("episode_finished")
    assert kinds.count("result") == 1 and kinds.count("decision") == 1


def test_a_read_error_keeps_the_events_and_retries() -> None:
    module = _module()
    weave = FakeWeave(_episode())
    feed = module.WeaveFeed(weave, run_id=RUN)
    feed.poll()

    weave.fail = True
    assert feed.poll() == []
    assert "weave unreachable" in (feed.last_error or "")
    assert len(feed.events) == 1

    weave.fail = False
    weave.calls.append(_action())
    assert [event["kind"] for event in feed.poll()] == ["decision"]
    assert feed.last_error is None


def test_an_unparseable_action_reply_is_shown_as_unusable() -> None:
    module = _module()
    broken = _action()
    broken.inputs["response_text"] = ""
    broken.inputs["finish_reason"] = "length"

    (event,) = [e for e in module.WeaveFeed(FakeWeave(broken), run_id=RUN).poll()]

    assert event["kind"] == "decision"
    assert event["action"] is None
    assert event["finish_reason"] == "length"


def _serve(module: ModuleType, feed: Any) -> tuple[Any, str]:
    server = module.make_server(feed, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}"


def test_the_server_serves_the_page_and_an_incremental_feed() -> None:
    module = _module()
    feed = module.WeaveFeed(FakeWeave(_episode(), _action(), _step(), _build()), run_id=RUN)
    feed.poll()
    server, base = _serve(module, feed)
    try:
        page = urllib.request.urlopen(f"{base}/", timeout=5).read().decode()
        everything = json.loads(urllib.request.urlopen(f"{base}/events", timeout=5).read())
        later = json.loads(urllib.request.urlopen(f"{base}/events?after=1", timeout=5).read())
    finally:
        server.shutdown()

    assert "<title>" in page and RUN in page
    assert [event["index"] for event in everything["events"]] == [0, 1, 2, 3]
    assert [event["index"] for event in later["events"]] == [2, 3]
    assert everything["run_id"] == RUN


def test_the_feed_never_serves_prompt_or_system_text() -> None:
    module = _module()
    feed = module.WeaveFeed(FakeWeave(_episode(), _action(), _build()), run_id=RUN)
    feed.poll()
    server, base = _serve(module, feed)
    try:
        body = urllib.request.urlopen(f"{base}/events", timeout=5).read().decode()
    finally:
        server.shutdown()

    assert PROMPT_MARK not in body and SYSTEM_MARK not in body
    for event in json.loads(body)["events"]:
        assert "prompt" not in event and "system" not in event


def test_the_page_renders_model_text_without_html_injection() -> None:
    module = _module()

    page = module.render_page(run_id="<script>alert(1)</script>")

    assert "<script>alert(1)</script>" not in page
    assert "textContent" in page and "innerHTML" not in page
