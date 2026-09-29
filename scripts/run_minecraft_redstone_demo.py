"""Recorded, non-benchmark cold versus skill reuse on one redstone task."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path


def harness():
    name = "redstone_easy_harness"
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).with_name("run_minecraft_easy_live_smoke.py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    module.SCENARIO_ID = "redstone-lamp-v1"
    module.CONDITION = "non-benchmark-minecraft-redstone-comparison"
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


async def run():
    m = harness()
    settings = m.IntegrationSettings.from_environ(m.os.environ)
    if not settings.trace.enabled or not settings.model.inference_model:
        raise RuntimeError("Configured model and Weave tracing are required")
    run_id = datetime.now(UTC).strftime("minecraft-redstone-%Y%m%dT%H%M%SZ")
    database = Path(f".noob-agent/{run_id}.sqlite3")
    database.parent.mkdir(exist_ok=True)
    trace = m.build_trace_sink(settings.trace, wandb=settings.wandb)
    summary = {}
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
                call_budget=30,
                token_budget=1_100_000,
            )
            try:
                async with asyncio.timeout(600):
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
            costs = {}
            for call in records:
                phase = (
                    "builder"
                    if call.purpose in {"build", "repair"}
                    else ("reuse" if call.experiment_id.endswith("-reuse") else "cold")
                )
                costs[phase] = costs.get(phase, 0) + m._call_tokens(call)
            payload = {
                "run_id": run_id,
                "status": status,
                "database": str(database),
                "comparison": "same-task cold versus skill reuse; not held-out transfer",
                "tokens_by_phase": costs,
                "sequence": summary,
            }
            output = database.with_suffix(".json")
            output.write_text(json.dumps(payload, indent=2) + "\n")
            print(json.dumps(payload, indent=2), flush=True)
    finally:
        trace.flush()


if __name__ == "__main__":
    asyncio.run(run())
