"""Serve a live local view of a run's reasoning, read from Weave as it arrives.

Section 23 step 23b of `hackathon_plan.md`. The view polls Weave, never SQLite,
for the `noob_agent.*` calls of one run: each Action decision's subgoal,
expected evidence, action, and result; the Builder's calls and code; and episode
outcomes. It serves a page and a JSON event feed on 127.0.0.1 for display beside
the game. It is display only and never serves prompt or system text.

    uv run --env-file .env python scripts/live_reasoning_view.py --run-id <sequence-id>
"""

# The embedded page's HTML and JavaScript keep their natural line lengths.
# ruff: noqa: E501

from __future__ import annotations

import argparse
import html
import importlib
import json
import re
import sys
import threading
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

DEFAULT_PORT = 8765
# Weave made new calls queryable in 0.8-3.4 s when measured, so polling faster than
# this only adds load.
POLL_SECONDS = 0.5
_PYTHON_BLOCK = re.compile(r"```python\s*\n(.*?)```", re.DOTALL)
_JSON_BLOCK = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)


def _op(call: Any) -> str:
    """`weave:///entity/project/op/noob_agent.step:digest` -> `step`."""
    name = str(getattr(call, "op_name", "") or "")
    return name.rsplit("/", 1)[-1].split(":", 1)[0].removeprefix("noob_agent.")


def _inputs(call: Any) -> dict[str, Any]:
    inputs = getattr(call, "inputs", None)
    return dict(inputs) if isinstance(inputs, dict) or hasattr(inputs, "items") else {}


def _decision(text: object) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        return {}
    start = text.find("{")
    if start == -1:
        return {}
    try:
        parsed, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _skill_name(text: str) -> str | None:
    match = _JSON_BLOCK.search(text)
    if match is None:
        return None
    try:
        metadata = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    name = metadata.get("name") if isinstance(metadata, dict) else None
    return name if isinstance(name, str) else None


def _started(call: Any) -> str:
    return str(getattr(call, "started_at", "") or "")


def _latest(calls: Sequence[Any], cursor: Any) -> Any:
    for call in calls:
        started = getattr(call, "started_at", None)
        if started is not None and (cursor is None or started > cursor):
            cursor = started
    return cursor


def _query(match: dict[str, Any], cursor: Any) -> dict[str, Any]:
    if cursor is not None:
        # Inclusive, so calls sharing the cursor's start time are kept; seen IDs dedupe.
        match = {
            "$and": [match, {"$gte": [{"$getField": "started_at"}, {"$literal": str(cursor)}]}]
        }
    return {
        "query": {"$expr": match},
        "sort_by": [{"field": "started_at", "direction": "asc"}],
        "limit": 500,
    }


class WeaveFeed:
    """Turns one run's Weave calls into display events, incrementally and without duplicates."""

    def __init__(self, client: Any, *, run_id: str) -> None:
        self._client = client
        self.run_id = run_id
        self.events: list[dict[str, Any]] = []
        self.last_error: str | None = None
        self._seen: set[str] = set()
        self._episodes: set[str] = set()
        self._finished: set[str] = set()
        self._run_cursor: Any = None
        self._step_cursor: Any = None
        self._lock = threading.Lock()

    def events_after(self, index: int) -> list[dict[str, Any]]:
        with self._lock:
            return [event for event in self.events if event["index"] > index]

    def poll(self) -> list[dict[str, Any]]:
        """Fetch new calls and return the events they added; keep everything on error.

        Three reads per poll. Root calls (episodes and Builder calls) are read
        without a cursor, because an open episode call may only become queryable
        when it finishes. The run's calls are read from a time cursor. Steps
        carry only an episode ID, so they are read for the known episodes with
        their own cursor, which never runs ahead of an episode's first call.
        """
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                roots_read = pool.submit(self._read, self._roots_query())
                run_read = pool.submit(self._read, self._run_query())
                roots, run_calls = roots_read.result(), run_read.result()
            for call in [*roots, *run_calls]:
                self._learn_episode(call)
            step_calls = self._read(self._step_query()) if self._episodes else []
        except Exception as error:  # a Weave read failure must not end the view
            self.last_error = f"{type(error).__name__}: {error}"
            return []
        self.last_error = None

        self._run_cursor = _latest(run_calls, self._run_cursor)
        self._step_cursor = _latest(step_calls, self._step_cursor)
        by_id = {str(getattr(call, "id", "")): call for call in [*roots, *run_calls, *step_calls]}
        added: list[dict[str, Any]] = []
        for call_id, call in sorted(by_id.items(), key=lambda item: _started(item[1])):
            if not call_id or call_id in self._seen or not self._belongs(call):
                continue
            self._seen.add(call_id)
            added.extend(self._events_for(call))
        for call in roots:
            call_id = str(getattr(call, "id", ""))
            output = getattr(call, "output", None)
            if (
                _op(call) == "episode"
                and call_id in self._seen
                and call_id not in self._finished
                and isinstance(output, dict)
                and output
            ):
                self._finished.add(call_id)
                added.append(
                    {
                        "kind": "episode_finished",
                        "call_id": call_id,
                        "episode_id": _inputs(call).get("episode_id"),
                        "stop_reason": output.get("stop_reason"),
                        "total_decisions": output.get("total_decisions"),
                        "total_primitives": output.get("total_primitives"),
                    }
                )

        with self._lock:
            for event in added:
                event["index"] = len(self.events)
                self.events.append(event)
        return added

    def _read(self, request: dict[str, Any]) -> list[Any]:
        return list(self._client.get_calls(**request))

    def _roots_query(self) -> dict[str, Any]:
        return {**_query(self._by_run(), None), "filter": {"trace_roots_only": True}}

    def _learn_episode(self, call: Any) -> None:
        inputs = _inputs(call)
        episode_id = inputs.get("episode_id")
        if not isinstance(episode_id, str) or not self._belongs(call):
            return
        self._episodes.add(episode_id)
        # Steps follow their episode's start and each Action call. A Builder call
        # names a finished episode, and a call seen before cannot move the cursor back.
        if str(getattr(call, "id", "")) in self._seen:
            return
        if _op(call) == "model_call" and inputs.get("purpose") != "action":
            return
        started = getattr(call, "started_at", None)
        if started is not None and (self._step_cursor is None or started < self._step_cursor):
            self._step_cursor = started

    def _by_run(self) -> dict[str, Any]:
        return {
            "$contains": {
                "input": {"$getField": "inputs.experiment_id"},
                "substr": {"$literal": self.run_id},
            }
        }

    def _run_query(self) -> dict[str, Any]:
        return _query(self._by_run(), self._run_cursor)

    def _step_query(self) -> dict[str, Any]:
        match: dict[str, Any] = {
            "$in": [
                {"$getField": "inputs.episode_id"},
                [{"$literal": episode} for episode in sorted(self._episodes)],
            ]
        }
        return _query(match, self._step_cursor)

    def _belongs(self, call: Any) -> bool:
        inputs = _inputs(call)
        experiment = inputs.get("experiment_id")
        if isinstance(experiment, str) and experiment.startswith(self.run_id):
            return True
        return inputs.get("episode_id") in self._episodes

    def _events_for(self, call: Any) -> list[dict[str, Any]]:
        inputs = _inputs(call)
        base = {
            "call_id": str(call.id),
            "url": getattr(call, "ui_url", None),
            "episode_id": inputs.get("episode_id"),
        }
        op = _op(call)
        if op == "episode":
            episode_id = inputs.get("episode_id")
            if isinstance(episode_id, str):
                self._episodes.add(episode_id)
            return [
                {
                    **base,
                    "kind": "episode",
                    "experiment_id": inputs.get("experiment_id"),
                    "scenario_id": inputs.get("scenario_id"),
                    "seed": inputs.get("seed"),
                    "split": inputs.get("split"),
                    "public_goal": inputs.get("public_goal"),
                }
            ]
        if op == "step":
            return [
                {
                    **base,
                    "kind": "result",
                    "action_id": inputs.get("action_id"),
                    "sequence": inputs.get("sequence"),
                    "tool_name": inputs.get("tool_name"),
                    "arguments": inputs.get("arguments"),
                    "status": inputs.get("status"),
                    "code": inputs.get("code"),
                    "message": inputs.get("message"),
                    "terminal": inputs.get("terminal"),
                }
            ]
        if op != "model_call":
            return []
        episode_id = inputs.get("episode_id")
        if isinstance(episode_id, str):
            self._episodes.add(episode_id)
        usage = {
            "finish_reason": inputs.get("finish_reason"),
            "input_tokens": inputs.get("input_tokens"),
            "output_tokens": inputs.get("output_tokens"),
            "latency_ms": inputs.get("latency_ms"),
            "error": inputs.get("error"),
            "reasoning": inputs.get("reasoning"),
        }
        if inputs.get("purpose") == "action":
            decision = _decision(inputs.get("response_text"))
            action = decision.get("action") or decision.get("tool") or decision.get("skill")
            return [
                {
                    **base,
                    **usage,
                    "kind": "decision",
                    "action_id": inputs.get("action_id"),
                    "subgoal": decision.get("subgoal"),
                    "expected_evidence": decision.get("expected_evidence"),
                    "action": action if isinstance(action, str) else None,
                    "arguments": decision.get("arguments") or decision.get("inputs") or {},
                    "finding": decision.get("finding"),
                }
            ]
        text = inputs.get("response_text")
        text = text if isinstance(text, str) else ""
        code = _PYTHON_BLOCK.search(text)
        return [
            {
                **base,
                **usage,
                "kind": "builder",
                "purpose": inputs.get("purpose"),
                "skill_name": _skill_name(text),
                "code": code.group(1) if code else None,
            }
        ]


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>noob-agent live reasoning: __RUN__</title>
<style>
:root { --bg:#0d1016; --panel:#161b24; --line:#262e3b; --ink:#e6e9ef; --dim:#8b94a7;
        --accent:#ffb454; --ok:#7fd962; --bad:#f07178; --info:#59c2ff; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
       font:14px/1.45 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif; }
header { display:flex; flex-wrap:wrap; gap:.5rem 1rem; align-items:baseline; padding:12px 16px;
         border-bottom:1px solid var(--line); }
header h1 { margin:0; font-size:15px; letter-spacing:.02em; }
header .run { color:var(--dim); font-family:ui-monospace,monospace; }
#status { margin-left:auto; color:var(--dim); font-size:12px; }
#status.error { color:var(--bad); }
main { display:grid; grid-template-columns:minmax(0,3fr) minmax(0,2fr); gap:12px; padding:12px 16px; }
@media (max-width: 900px) { main { grid-template-columns:1fr; } }
section { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:12px; min-width:0; }
h2 { margin:0 0 8px; font-size:12px; text-transform:uppercase; letter-spacing:.08em; color:var(--dim); }
#now .episode { color:var(--dim); font-size:12px; }
#now .subgoal { font-size:22px; line-height:1.25; margin:6px 0; }
#now .evidence { color:var(--dim); }
.chip { display:inline-block; font-family:ui-monospace,monospace; background:#0f141c; border:1px solid var(--line);
        border-radius:6px; padding:2px 6px; margin:6px 6px 0 0; }
.chip.action { color:var(--accent); }
.ok { color:var(--ok); } .bad { color:var(--bad); } .info { color:var(--info); }
ol { list-style:none; margin:0; padding:0; max-height:62vh; overflow:auto; }
li { border-top:1px solid var(--line); padding:8px 0; }
li:first-child { border-top:0; }
.meta { color:var(--dim); font-size:12px; }
a { color:var(--info); text-decoration:none; } a:hover { text-decoration:underline; }
pre { background:#0f141c; border:1px solid var(--line); border-radius:6px; padding:8px; overflow:auto;
      max-height:260px; font-size:12px; }
details summary { cursor:pointer; color:var(--dim); font-size:12px; }
</style></head>
<body>
<header><h1>noob-agent · live reasoning from Weave</h1><span class="run">__RUN__</span>
<span id="status">connecting to Weave…</span></header>
<main>
  <div>
    <section id="now"><h2>Now</h2><div class="episode" id="now-episode">Waiting for the first episode…</div>
      <div class="subgoal" id="now-subgoal"></div><div class="evidence" id="now-evidence"></div>
      <div id="now-action"></div></section>
    <section style="margin-top:12px"><h2>Decisions</h2><ol id="timeline"></ol></section>
  </div>
  <div>
    <section><h2>Builder</h2><ol id="builder"></ol></section>
    <section style="margin-top:12px"><h2>Episodes</h2><ol id="episodes"></ol></section>
  </div>
</main>
<script>
const RUN = __RUN_JSON__;
let after = -1;
const episodes = {};
const decisions = {};
function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}
function link(url) {
  const a = el("a", "", "Weave ↗");
  if (url) { a.href = url; a.target = "_blank"; a.rel = "noopener"; }
  return a;
}
function args(value) { return value && Object.keys(value).length ? JSON.stringify(value) : ""; }
function statusClass(status) { return status === "succeeded" ? "ok" : (status ? "bad" : "meta"); }
function episodeLabel(id) {
  const e = episodes[id];
  return e ? `${e.split} · ${e.scenario_id} · seed ${e.seed}` : (id || "");
}
function onEpisode(e) {
  episodes[e.episode_id] = e;
  const li = el("li"); li.id = "ep-" + e.call_id;
  li.append(el("div", "", episodeLabel(e.episode_id)), el("div", "meta", "running…"), link(e.url));
  document.getElementById("episodes").prepend(li);
  document.getElementById("now-episode").textContent = episodeLabel(e.episode_id);
}
function onFinished(e) {
  const li = document.getElementById("ep-" + e.call_id);
  if (li) li.children[1].textContent =
    `${e.stop_reason} · ${e.total_decisions} decisions · ${e.total_primitives} primitives`;
}
function onDecision(e) {
  const li = el("li");
  const head = el("div");
  head.append(el("span", "chip action", `${e.action || "unusable"}(${args(e.arguments)})`));
  const result = el("span", "chip meta", "…"); head.append(result);
  li.append(head, el("div", "", e.subgoal || ""), el("div", "meta", e.expected_evidence || ""));
  const meta = el("div", "meta",
    `${e.action_id || ""} · ${e.latency_ms ?? "?"} ms · ${e.input_tokens ?? "?"}/${e.output_tokens ?? "?"} tokens · ${e.finish_reason || ""} `);
  meta.append(link(e.url)); li.append(meta);
  if (e.reasoning) { const d = el("details"); d.append(el("summary", "", "model reasoning"), el("pre", "", e.reasoning)); li.append(d); }
  document.getElementById("timeline").prepend(li);
  decisions[`${e.episode_id}/${e.action_id}`] = result;
  document.getElementById("now-episode").textContent = episodeLabel(e.episode_id);
  document.getElementById("now-subgoal").textContent = e.subgoal || "(no usable decision)";
  document.getElementById("now-evidence").textContent = e.expected_evidence ? "expects: " + e.expected_evidence : "";
  const now = document.getElementById("now-action"); now.replaceChildren(
    el("span", "chip action", `${e.action || "unusable"}(${args(e.arguments)})`));
}
function onResult(e) {
  const chip = decisions[`${e.episode_id}/${e.action_id}`];
  const text = `${e.status}/${e.code}${e.terminal ? " · terminal" : ""}`;
  if (chip) { chip.textContent = text; chip.className = "chip " + statusClass(e.status); }
  const now = document.getElementById("now-action");
  if (now.children.length === 1) now.append(el("span", "chip " + statusClass(e.status), text));
}
function onBuilder(e) {
  const li = el("li");
  li.append(el("div", "", `${e.purpose}${e.skill_name ? " · " + e.skill_name : ""}`));
  const meta = el("div", "meta",
    `${e.finish_reason || e.error || ""} · ${e.latency_ms ?? "?"} ms · ${e.output_tokens ?? "?"} output tokens `);
  meta.append(link(e.url)); li.append(meta);
  if (e.code) li.append(el("pre", "", e.code));
  if (e.reasoning) { const d = el("details"); d.append(el("summary", "", "model reasoning"), el("pre", "", e.reasoning)); li.append(d); }
  document.getElementById("builder").prepend(li);
}
const handlers = { episode: onEpisode, episode_finished: onFinished, decision: onDecision,
                   result: onResult, builder: onBuilder };
async function tick() {
  try {
    const response = await fetch(`/events?after=${after}`, { cache: "no-store" });
    const body = await response.json();
    for (const event of body.events) { (handlers[event.kind] || (() => {}))(event); after = event.index; }
    const status = document.getElementById("status");
    status.textContent = body.last_error ? "Weave read failed, retrying: " + body.last_error
                                         : `live · ${after + 1} events from Weave`;
    status.className = body.last_error ? "error" : "";
  } catch (error) {
    const status = document.getElementById("status");
    status.textContent = "view server unreachable"; status.className = "error";
  }
  setTimeout(tick, 700);
}
tick();
</script></body></html>
"""


def render_page(*, run_id: str) -> str:
    return _PAGE.replace("__RUN_JSON__", json.dumps(run_id).replace("<", "\\u003c")).replace(
        "__RUN__", html.escape(run_id)
    )


def make_server(feed: WeaveFeed, *, host: str, port: int) -> ThreadingHTTPServer:
    page = render_page(run_id=feed.run_id).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - the stdlib names this method
            url = urlparse(self.path)
            if url.path == "/":
                self._send(200, "text/html; charset=utf-8", page)
            elif url.path == "/events":
                raw = parse_qs(url.query).get("after", ["-1"])[0]
                after = int(raw) if raw.lstrip("-").isdigit() else -1
                body = {
                    "run_id": feed.run_id,
                    "last_error": feed.last_error,
                    "events": feed.events_after(after),
                }
                self._send(200, "application/json", json.dumps(body, default=str).encode())
            else:
                self._send(404, "text/plain", b"not found")

        def _send(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    return ThreadingHTTPServer((host, port), Handler)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--run-id", required=True, help="A sequence ID or a prefix of several.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--project", default=None, help="Defaults to WANDB_PROJECT.")
    args = parser.parse_args(argv)

    from noob_agent.settings import IntegrationSettings

    project = args.project or IntegrationSettings.from_environ().wandb.project
    client = importlib.import_module("weave").init(project)
    feed = WeaveFeed(client, run_id=args.run_id)

    def poll_forever() -> None:
        while True:
            started = time.monotonic()
            feed.poll()
            time.sleep(max(0.0, POLL_SECONDS - (time.monotonic() - started)))

    threading.Thread(target=poll_forever, daemon=True).start()
    server = make_server(feed, host="127.0.0.1", port=args.port)
    print(f"Live reasoning view for {args.run_id}: http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
