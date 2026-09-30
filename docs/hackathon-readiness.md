# Hackathon submission readiness

The Most Production-Ready prize requires attendance and check-in by **4:15pm
Pacific on Thursday, October 1, 2026**, according to Alexa's invitation supplied
by the entrant. Plan to join by 3:00pm; presentations begin at 3:20pm, and awards
run 4:30–4:50pm. A winning team that is absent loses eligibility to the next team.

Attend at the Expo Stage, Moscone, **747 Howard St**, back left of the Expo Hall,
and check in with Anna (CoreWeave) or Alexa (AGI House). Alternatively, join
[Zoom](https://coreweave.zoom.us/j/85491824907), meeting ID **85491824907**, and
confirm check-in. Contact: [Alexa](mailto:alexa@agihouse.org).

## Isolated worktree

Branch: `codex/hackathon-production-ready`.
WSL checkout: `/home/nathan/noob-agent-hackathon-ready`.
This checkout starts from the live WSL source commit plus a snapshot of its
modified source, scripts, and tests. Live secrets, world saves, trial records,
recordings, and construction ownership stay in the original checkout. A separate
Git worktree does not isolate Minecraft: never launch competing world writers.

## Reproducible software checks

Use Python 3.11 or later, Node 22 or later, npm, and uv. From the checkout, run:

```sh
bash scripts/verify_hackathon.sh
```

This installs both npm dependency trees and runs lint, type checking, Python
tests, and JavaScript tests. It does not connect to Minecraft or model providers.
Passing this command establishes software regression coverage, not a functioning
redstone computer.

## Evidence gates

From the isolated checkout, inspect the authoritative trial without changing it:

```sh
uv run python scripts/check_hackathon_readiness.py \
  --manifest /home/nathan/noob-agent/.noob-agent/redstone-trials/20260930T020615Z-assisted-production/manifest.json \
  --video /absolute/path/to/final-demo.mp4 \
  --output docs/latest-readiness.json
```

Exit 0 means the retained hardware evidence and video metadata gates pass. Exit 2
means evidence is missing, invalid, or incomplete. This is a report, not a fresh
live grader. It requires durable observed module and full-machine grader records;
module names or optimistic status flags alone cannot produce a ready result.
Unresolved non-provider operations and construction after the final machine grade
block readiness. Historical unknown provider calls remain disclosed.

The final machine must pass register, arithmetic, storage/control, visible output,
and integration checks. Show both programs on the same hardware: output **3,8**
and **14,3**, including overflow, real instruction storage, reset preservation,
halt, and stable hold. A ripple-adder truth table is a useful diagnostic, but it
does not establish stored-program execution or arithmetic-module acceptance.

The final video is 90–120 seconds, 1920×1080, 60fps, H.264 in MP4 with AAC audio.
Silent AAC is permitted. Preserve original Replay footage. Review playback,
legibility, genuine normal-speed inputs and outputs, and provenance manually;
ffprobe verifies metadata only. Rehearse the live demo and backup playback.

## Known limitations

The inherited handoff reports only the register accepted by its full 49-case
Minecraft suite. It also reports passing full-adder and ripple-adder diagnostics,
unfinished latch integration, and no completed final video. Use the current
readiness report for updated evidence; do not present this handoff as a live grade.

The initial sidecar audit reported six moderate advisories through the pinned
Mineflayer authentication dependency tree. This branch overrides UUID to 11.1.1,
the patched CommonJS-compatible release identified in the
[maintainer advisory](https://github.com/uuidjs/uuid/security/advisories/GHSA-w5hq-g745-h8pq).
Both npm dependency trees now audit with zero vulnerabilities. Imports, UUID
generation, and Microsoft authentication-client construction passed locally.
Mineflayer stays at 4.39.0. A live connection smoke is still required before this
dependency change is adopted into the active demo checkout.

When construction is running, unknown operations can be in-flight requests.
Rerun the report at an idle boundary; do not interpret one read during an active
build as proof of a crashed or corrupted world.

Keep direct design/construction assistance and expanded production budgets in
the trial provenance. Describe the current run as assisted production; do not
represent it as the original unaided benchmark. Never claim a prize is assured.
