"""Record a side-by-side Doom video: cold DeepSeek against DeepSeek with its learned skill.

Non-benchmark demo tooling. It runs the current multi-round improvement loop
(`scripts/loop_bench.py run --rounds 2` on `basic-v3`) once to learn and accept
a skill, then plays the same held-out cells twice in a visible ViZDoom window:
once cold, with primitives only, and once offered the accepted skill. Both runs
use the frozen held-out budgets, the unchanged Action agent, and the independent
Doom grader. Every rendered frame was captured from the live game, and each
side's clock is its real elapsed episode time, including model latency.

    NOOB_AGENT_SANDBOX_MODE=local uv run --env-file .env \\
      python scripts/record_doom_comparison.py

Re-render a finished capture without calling a model:

    uv run python scripts/record_doom_comparison.py --render-from .noob-agent/videos/<run-id>
"""

from __future__ import annotations

import argparse
import asyncio
import bisect
import json
import math
import os
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_MANIFEST = Path("scenarios/doom/basic-v3/manifest.json")
DEFAULT_OUTPUT_DIR = Path(".noob-agent/videos")
DEFAULT_CELLS = ("doom-basic-heldout-a:101", "doom-basic-heldout-b:201")
DEMO_CONDITION = "non-benchmark-demo-video"
FPS = 30
SCALE = 2
HEADER = 64
FOOTER = 84
HOLD_SECONDS = 2.5
CARD_SECONDS = 5.0
THINKING_GAP_SECONDS = 0.25
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def frame_index_at(times: Sequence[float], t: float) -> int:
    """The latest frame captured at or before `t`, or the first frame before it."""
    return max(bisect.bisect_right(times, t) - 1, 0)


def output_frame_count(
    left_seconds: float, right_seconds: float, *, fps: int, hold_seconds: float
) -> int:
    return math.ceil((max(left_seconds, right_seconds) + hold_seconds) * fps)


def clock_label(seconds: float) -> str:
    return f"{math.floor(seconds * 10) / 10:.1f} s"


def verdict_label(goal_completed: bool, stop_reason: str, seconds: float) -> str:
    if goal_completed:
        return f"GOAL COMPLETE in {clock_label(seconds)}"
    return f"FAILED: {stop_reason}"


@dataclass
class SideSummary:
    label: str
    episode_id: str
    goal_completed: bool
    stop_reason: str
    seconds: float
    decisions: int
    primitives: int
    skill_uses: int
    tokens: int


# ---------------------------------------------------------------- live capture


class _CapturingGame:
    """Delegates to a `DoomGame` and calls `grab` after every `make_action`."""

    def __init__(self, game: Any, grab: Any) -> None:
        self._inner = game
        self._grab = grab

    def make_action(self, values: Any, ticks: int = 1) -> Any:
        reward = self._inner.make_action(values, ticks)
        self._grab()
        return reward

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _capturing_connector(sink: dict[str, Any], visible: bool) -> Any:
    import numpy as np

    from noob_agent.connectors.doom import DoomConnector, DoomSettings

    class CapturingDoomConnector(DoomConnector):
        """Display only: copies every rendered frame; game behaviour is unchanged."""

        def _grab(self) -> None:
            game = self._game
            state = game.get_state() if game is not None else None
            if state is None or state.screen_buffer is None:
                return
            buffer = np.asarray(state.screen_buffer)
            if buffer.ndim == 3 and buffer.shape[0] == 3:
                buffer = buffer.transpose(1, 2, 0)
            sink["times"].append(time.monotonic() - sink["t0"])
            sink["frames"].append(np.ascontiguousarray(buffer, dtype=np.uint8))
            sink["labels"].append(sink["label"])

        async def reset(self, scenario_id: str, seed: int) -> Any:
            observation = await super().reset(scenario_id, seed)
            if self._game is not None and not isinstance(self._game, _CapturingGame):
                self._game = _CapturingGame(self._game, self._grab)
            sink["t0"] = time.monotonic()
            sink["label"] = "start"
            self._grab()
            return observation

        async def step(self, request: Any) -> Any:
            prefix = "[skill] " if "." in request.action_id else ""
            arguments = " ".join(str(value) for value in request.arguments.values())
            sink["label"] = f"{prefix}{request.tool_name} {arguments}".strip()
            return await super().step(request)

    return CapturingDoomConnector(DoomSettings(window_visible=visible, realtime=True))


async def _play(
    *,
    label: str,
    cell: tuple[str, int],
    offered: tuple[Any, ...],
    registry: Any,
    store: Any,
    client: Any,
    executor: Any,
    trace: Any,
    settings: Any,
    run_id: str,
    visible: bool,
    capture_dir: Path,
) -> SideSummary:
    import numpy as np

    from noob_agent.connectors.doom import CONNECTOR_VERSION
    from noob_agent.grading.doom import DoomPrivateOutcome, grade_doom_episode
    from noob_agent.models.recording import RecordingModelClient
    from noob_agent.runtime.heldout import HeldOutRunner, heldout_experiment

    scenario_id, seed = cell
    side = "skill" if offered else "cold"
    experiment = heldout_experiment(
        experiment_id=f"{run_id}-{side}-{scenario_id}-{seed}",
        model_id=settings.model.inference_model,
        connector_version=CONNECTOR_VERSION,
        created_at=datetime.now(UTC),
        condition=f"{DEMO_CONDITION}-{side}",
    )
    store.create_experiment(experiment)
    sink: dict[str, Any] = {"times": [], "frames": [], "labels": [], "t0": 0.0, "label": ""}
    connector = _capturing_connector(sink, visible)
    opened: list[str] = []
    runner = HeldOutRunner(
        connector=connector,
        store=store,
        registry=registry,
        client=RecordingModelClient(
            client,
            store=store,
            experiment_id=experiment.experiment_id,
            role="action",
            model_id=settings.model.inference_model,
            trace=trace,
            episode_source=lambda: opened[-1] if opened else None,
        ),
        executor=executor,
        trace=trace,
        action_max_output_tokens=settings.model.action_max_output_tokens,
        action_thinking=settings.model.action_thinking,
        on_episode_started=opened.append,
        offered=offered,
    )
    print(f"{label}: {scenario_id} seed {seed}", file=sys.stderr, flush=True)
    result = await runner.run(experiment=experiment, scenario_id=scenario_id, seed=seed)
    seconds = time.monotonic() - sink["t0"]
    episode_id = result.episode.episode_id
    grade = grade_doom_episode(
        store.read_episode(episode_id),
        DoomPrivateOutcome.from_connector(episode_id, connector.private_outcome()),
    )
    summary = SideSummary(
        label=label,
        episode_id=episode_id,
        goal_completed=bool(grade.goal_completed),
        stop_reason=str(result.episode.stop_reason),
        seconds=seconds,
        decisions=result.episode.decisions_used,
        primitives=result.episode.primitives_used,
        skill_uses=len(result.skill_uses),
        tokens=result.input_tokens + result.output_tokens,
    )
    np.savez_compressed(
        capture_dir / f"{scenario_id}-{seed}-{side}.npz",
        frames=np.stack(sink["frames"]),
        times=np.asarray(sink["times"]),
        labels=np.asarray(sink["labels"]),
    )
    print(
        f"{label}: {verdict_label(summary.goal_completed, summary.stop_reason, seconds)}",
        file=sys.stderr,
        flush=True,
    )
    return summary


def _learn_and_play(args: argparse.Namespace, environ: Mapping[str, str]) -> Path | None:
    from noob_agent.connectors.doom import DoomConnector, DoomSettings
    from noob_agent.grading.doom import DoomPrivateOutcome, grade_doom_episode
    from noob_agent.models.client import build_model_client
    from noob_agent.observability.tracing import NullTraceSink, build_trace_sink, close_trace
    from noob_agent.runtime.improvement import ImprovementLoop
    from noob_agent.runtime.sequence import HeldOutCell
    from noob_agent.settings import IntegrationSettings
    from noob_agent.skills.executor import build_skill_executor
    from noob_agent.skills.registry import SkillRegistry
    from noob_agent.storage import EpisodeStore

    settings = IntegrationSettings.from_environ(environ)
    if settings.model.provider == "disabled" or settings.model.inference_model is None:
        print("Refusing to run: no model provider and model ID are configured.", file=sys.stderr)
        return None
    if settings.sandbox.mode == "disabled":
        print("Refusing to run: NOOB_AGENT_SANDBOX_MODE is disabled.", file=sys.stderr)
        return None

    run_id = datetime.now(UTC).strftime("doom-demo-video-%Y%m%dT%H%M%SZ")
    capture_dir = args.output_dir / run_id
    capture_dir.mkdir(parents=True, exist_ok=False)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    training: HeldOutCell | None = None
    practice: list[HeldOutCell] = []
    heldout: list[HeldOutCell] = []
    for scenario_id, scenario in manifest["scenarios"].items():
        cells = [HeldOutCell(scenario_id, int(seed)) for seed in scenario["seeds"]]
        if scenario["split"] == "training":
            training = cells[0]
            practice = [HeldOutCell(scenario_id, int(s)) for s in scenario["practice_seeds"]]
        else:
            heldout.extend(cells)
    assert training is not None
    demo_cells = []
    for text in args.cells:
        scenario_id, seed = text.rsplit(":", 1)
        demo_cells.append((scenario_id, int(seed)))

    def grade(stored: Any, connector: DoomConnector) -> object:
        outcome = DoomPrivateOutcome.from_connector(
            stored.episode.episode_id, connector.private_outcome()
        )
        return grade_doom_episode(stored, outcome)

    client = build_model_client(settings.model, settings.wandb)
    executor = build_skill_executor(settings.sandbox)
    trace = build_trace_sink(settings.trace, wandb=settings.wandb)
    trace = trace if trace is not None else NullTraceSink()
    model_id = settings.model.inference_model
    meta: dict[str, Any] = {"run_id": run_id, "model_id": model_id, "pairs": []}
    try:
        with EpisodeStore.open(capture_dir / f"{run_id}.sqlite3") as store:
            best: tuple[int, Any, Any, dict[str, Any]] | None = None
            for attempt in range(1, args.max_attempts + 1):
                print(
                    f"{run_id}: learning attempt {attempt} of {args.max_attempts} "
                    f"(improvement loop, rounds {args.rounds})",
                    file=sys.stderr,
                    flush=True,
                )
                registry = SkillRegistry()
                learning_started = time.monotonic()
                loop = ImprovementLoop(
                    max_rounds=args.rounds,
                    grade_success=lambda g: bool(getattr(g, "goal_completed", False)),
                    connector_factory=lambda: DoomConnector(DoomSettings()),
                    client=client,
                    model_id=model_id,
                    store=store,
                    registry=registry,
                    executor=executor,
                    grade=grade,
                    trace=trace,
                    action_max_output_tokens=settings.model.action_max_output_tokens,
                    builder_max_output_tokens=settings.model.builder_max_output_tokens,
                    action_thinking=settings.model.action_thinking,
                    builder_thinking=settings.model.builder_thinking,
                    condition=DEMO_CONDITION,
                    heldout_concurrency=6,
                )
                learned = asyncio.run(
                    loop.run(
                        sequence_id=f"{run_id}-learn-{attempt}",
                        training_scenario_id=training.scenario_id,
                        training_seed=training.seed,
                        practice=practice,
                        heldout=heldout,
                    )
                )
                version = learned.final_version
                heldout_goals = sum(
                    1 for e in learned.heldout if getattr(e.grade, "goal_completed", False)
                )
                meta["learning"] = {
                    "attempt": attempt,
                    "max_attempts": args.max_attempts,
                    "min_heldout_goals": args.min_heldout_goals,
                    "seconds": round(time.monotonic() - learning_started, 1),
                    "tokens": learned.learning_tokens,
                    "calls": learned.learning_calls,
                    "stop_reason": str(learned.stop_reason),
                    "skill": None if version is None else f"{version.name} v{version.version}",
                    "heldout_goals": heldout_goals,
                    "heldout_episodes": len(learned.heldout),
                    "cold_training_goal": bool(
                        getattr(learned.training.grade, "goal_completed", False)
                    ),
                }
                meta.setdefault("attempts", []).append(meta["learning"])
                print(json.dumps(meta["learning"]), file=sys.stderr, flush=True)
                if version is not None and (best is None or heldout_goals > best[0]):
                    best = (heldout_goals, version, registry, meta["learning"])
                if version is not None and heldout_goals >= args.min_heldout_goals:
                    break
            if best is None:
                print("No attempt accepted a skill; rerun to try again.", file=sys.stderr)
                return None
            heldout_goals, version, registry, meta["learning"] = best
            if heldout_goals < args.min_heldout_goals:
                print(
                    "No attempt met the threshold; filming the best attempt.",
                    file=sys.stderr,
                    flush=True,
                )
            for cell in demo_cells:
                common = {
                    "cell": cell,
                    "registry": registry,
                    "store": store,
                    "client": client,
                    "executor": executor,
                    "trace": trace,
                    "settings": settings,
                    "run_id": run_id,
                    "visible": not args.headless,
                    "capture_dir": capture_dir,
                }
                cold = asyncio.run(_play(label="DeepSeek cold", offered=(), **common))
                skilled = asyncio.run(
                    _play(label="DeepSeek + learned skill", offered=(version,), **common)
                )
                meta["pairs"].append(
                    {
                        "scenario_id": cell[0],
                        "seed": cell[1],
                        "cold": asdict(cold),
                        "skill": asdict(skilled),
                    }
                )
    finally:
        with suppress(Exception):
            close_trace(trace)
    (capture_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return capture_dir


# ---------------------------------------------------------------- rendering


class _Canvas:
    def __init__(self) -> None:
        from PIL import ImageFont

        self.width = 320 * SCALE * 2
        self.height = HEADER + 240 * SCALE + FOOTER
        self.big = ImageFont.truetype(FONT_BOLD, 30)
        self.mid = ImageFont.truetype(FONT_BOLD, 20)
        self.small = ImageFont.truetype(FONT, 16)

    def blank(self) -> Any:
        from PIL import Image

        return Image.new("RGB", (self.width, self.height), (14, 16, 22))


def _side_state(capture: Any, summary: dict[str, Any], t: float) -> tuple[int, str, str, Any]:
    times = capture["times"]
    done = t >= summary["seconds"]
    shown = min(t, summary["seconds"])
    index = frame_index_at(times, shown)
    status = str(capture["labels"][index])
    if done:
        verdict = verdict_label(
            summary["goal_completed"], summary["stop_reason"], summary["seconds"]
        )
        color = (70, 220, 120) if summary["goal_completed"] else (240, 90, 80)
        return index, clock_label(summary["seconds"]), verdict, color
    next_time = times[index + 1] if index + 1 < len(times) else summary["seconds"]
    if next_time - times[index] > THINKING_GAP_SECONDS and shown > times[index]:
        status = "DeepSeek thinking..."
    return index, clock_label(shown), status, (230, 230, 230)


def _render_pair(
    canvas: _Canvas, pair: dict[str, Any], captures: dict[str, Any], skill_name: str, write: Any
) -> None:
    from PIL import Image, ImageDraw

    count = output_frame_count(
        pair["cold"]["seconds"], pair["skill"]["seconds"], fps=FPS, hold_seconds=HOLD_SECONDS
    )
    titles = {
        "cold": "DeepSeek cold (primitives only)",
        "skill": f"DeepSeek + learned skill ({skill_name})",
    }
    for n in range(count):
        t = n / FPS
        image = canvas.blank()
        draw = ImageDraw.Draw(image)
        for column, side in enumerate(("cold", "skill")):
            x = column * 320 * SCALE
            capture = captures[side]
            index, clock, status, color = _side_state(capture, pair[side], t)
            frame = Image.fromarray(capture["frames"][index]).resize(
                (320 * SCALE, 240 * SCALE), Image.Resampling.NEAREST
            )
            image.paste(frame, (x, HEADER))
            draw.text((x + 14, 8), titles[side], font=canvas.mid, fill=(255, 255, 255))
            draw.text(
                (x + 14, 36),
                f"{pair['scenario_id']}  seed {pair['seed']}  "
                "(held-out: never seen while learning)",
                font=canvas.small,
                fill=(160, 170, 185),
            )
            footer = HEADER + 240 * SCALE
            draw.text((x + 14, footer + 8), clock, font=canvas.big, fill=(255, 210, 80))
            draw.text((x + 14, footer + 50), status, font=canvas.mid, fill=color)
        draw.line([(320 * SCALE, 0), (320 * SCALE, canvas.height)], fill=(60, 64, 76), width=3)
        write(image)


def _render_card(canvas: _Canvas, lines: Sequence[tuple[str, Any]], write: Any) -> None:
    from PIL import ImageDraw

    image = canvas.blank()
    draw = ImageDraw.Draw(image)
    y = 40
    for text, font in lines:
        draw.text((48, y), text, font=font, fill=(240, 240, 240))
        y += font.size + 18
    for _ in range(int(CARD_SECONDS * FPS)):
        write(image)


def render(capture_dir: Path, output: Path | None = None) -> Path:
    import numpy as np

    meta = json.loads((capture_dir / "meta.json").read_text(encoding="utf-8"))
    canvas = _Canvas()
    output = output or capture_dir / f"{meta['run_id']}.mp4"
    ffmpeg = subprocess.Popen(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{canvas.width}x{canvas.height}",
            "-r",
            str(FPS),
            "-i",
            "-",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "20",
            str(output),
        ],
        stdin=subprocess.PIPE,
    )
    assert ffmpeg.stdin is not None

    def write(image: Any) -> None:
        ffmpeg.stdin.write(image.tobytes())

    learning = meta["learning"]
    _render_card(
        canvas,
        [
            ("noob-agent: can a model learn a game it has never seen?", canvas.big),
            (f"Model: {meta['model_id']}   Game: Doom (ViZDoom)", canvas.mid),
            ("Same model, same primitive controls, same held-out budget.", canvas.mid),
            (
                "Left: cold, primitives only.   Right: plus the skill it wrote and validated.",
                canvas.mid,
            ),
            (
                f"Learning loop: {learning['seconds']} s, {learning['tokens']:,} tokens, "
                f"{learning['calls']} model calls -> {learning['skill']} "
                f"(learning attempt {learning.get('attempt', 1)} of "
                f"{learning.get('max_attempts', 1)}; target "
                f"{learning.get('min_heldout_goals', 0)} held-out goals, else best attempt)",
                canvas.small,
            ),
            (
                "Clocks show real elapsed episode time, including model latency. Grades come from "
                "the independent Doom grader.",
                canvas.small,
            ),
        ],
        write,
    )
    for pair in meta["pairs"]:
        captures = {
            side: dict(np.load(capture_dir / f"{pair['scenario_id']}-{pair['seed']}-{side}.npz"))
            for side in ("cold", "skill")
        }
        _render_pair(canvas, pair, captures, learning["skill"], write)

    lines: list[tuple[str, Any]] = [("Held-out results (time to goal)", canvas.big)]
    for pair in meta["pairs"]:
        parts = []
        for side in ("cold", "skill"):
            s = pair[side]
            name = "cold" if side == "cold" else "skill"
            verdict = verdict_label(s["goal_completed"], s["stop_reason"], s["seconds"])
            parts.append(f"{name}: {verdict}, {s['primitives']} actions, {s['tokens']:,} tokens")
        lines.append((f"{pair['scenario_id']} seed {pair['seed']}", canvas.mid))
        lines.extend((f"   {part}", canvas.small) for part in parts)
    lines.append(
        (
            f"During learning the skill completed {learning['heldout_goals']} of "
            f"{learning['heldout_episodes']} held-out goals (current loop bench).",
            canvas.mid,
        )
    )
    lines.append(
        ("Non-benchmark demo run; see docs/loop-optimization.md for scorecards.", canvas.small)
    )
    _render_card(canvas, lines, write)
    ffmpeg.stdin.close()
    if ffmpeg.wait() != 0:
        raise RuntimeError("ffmpeg failed to encode the video.")
    return output


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--cells",
        nargs="+",
        default=list(DEFAULT_CELLS),
        help="Held-out cells to film, as scenario:seed.",
    )
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument(
        "--min-heldout-goals",
        type=int,
        default=0,
        help="Re-learn until the skill completes this many held-out goals (disclosed in video).",
    )
    parser.add_argument("--max-attempts", type=int, default=1)
    parser.add_argument("--headless", action="store_true", help="Hide the game window.")
    parser.add_argument("--render-from", type=Path, default=None)
    args = parser.parse_args(argv)

    capture_dir = args.render_from
    if capture_dir is None:
        capture_dir = _learn_and_play(args, os.environ if environ is None else environ)
        if capture_dir is None:
            return 1
    video = render(capture_dir)
    print(f"VIDEO: {video.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
