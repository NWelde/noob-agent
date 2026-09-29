# Computer demo fix handoff

## Objective

Fix the model-built Minecraft computer demo so the forced grading handoff is enforced by the planner response schema and one planner timeout gets exactly one bounded retry. Add focused tests first, run them, then resume the headless `computer` demo and append its trace/results to `logs/computer-module-live-monitor-2026-09-29.md`.

## Confirmed failure evidence

The two completed attempts are recorded in the live-monitor log and their manifests:

- Attempt 1: `.noob-agent/redstone-trials/20260929T095159-a08e5e901f974334a2ac4ea914c0c9e4/manifest.json`. The trusted reset succeeded, then the first planner request timed out at 60 seconds. No intention or world placement occurred.
- Attempt 2: `.noob-agent/redstone-trials/20260929T095414-aee4fe22ce8849a4b34d2e122afe368e/manifest.json`. Three construction intentions produced 14 effect-verified placements. On the forced handoff, the planner returned four more construction actions, rejected before dispatch. The subsequent planner call for the required declaration timed out at 60 seconds. No module declaration, checks, or grade occurred.

The actual target is the model-built computer task (`task=computer`), never the lamp-repair task. Server is expected to be running in tmux session `noob-agent-minecraft` on ports 25567/25577; recheck before resuming. Provider credentials are loaded with `uv run --env-file .env`; never print or record them.

## Requested implementation

1. In `src/noob_agent/redstone/planner.py`, when `PlannerContext.force_grading` is active, make the response schema require a non-null `module_inspection` and an empty `actions` array. Ensure the model-facing system instruction agrees. Leave ordinary construction and lamp repair schema behavior unchanged.
2. In `src/noob_agent/redstone/loop.py`, retry a planner call at most once when that call times out. Record the timeout and retry in the manifest. Charge each provider call to the existing planner-call budget and preserve the second timeout as a terminal failure. Do not retry other exceptions or validations. Do not dispatch any action from an incomplete/unknown response. Keep the configured 60-second per-call timeout.
3. First write focused tests that fail before the fix and cover the forced schema and timeout retry/budget/terminal cases. Implement the changes, run relevant tests, then the appropriate broader test suite if practical.

## Resume and logging

After the focused tests pass, inspect the CLI and use the existing provider-run workflow to resume the headless `computer` trial with the established planner model/project and persistent connected bot. Do not use `lamp_repair`. Continuously monitor the manifest/process and give progress updates. Append a timestamped Attempt 3 section to the existing live-monitor log, with actual intentions, accepted/rejected offers, verified placements, timeout retries, checks, terminal grade, and manifest path. Include the complete raw new manifest if consistent with the existing log format, and verify any embedded manifest is byte/structure-equivalent to its source. Do not claim success without a public grade.

## Workspace constraints

- Preserve existing user changes. At handoff time, `git status --short` showed only untracked `logs/` (the pre-existing run log); inspect fresh status before editing.
- Do not commit or push unless separately asked.
- Do not introduce credential data into code, handoff, tests, or logs.
- Final report should summarize code changes, tests, resumed run outcome, and manifest/log locations.
