---
name: production-ready-submission
description: Turn noob-agent into a winning "Most Production-Ready" submission for CoreWeave Hacks Part 2 (AGI House / Fully Connected, deadline September 29-30 2026 PT). Use whenever the user asks to make noob-agent production ready, harden it, prepare or record the demo video, polish the README for judges, package the submission, check W&B/Weave evidence, plan remaining hackathon days, or prep for the October 1 award ceremony. Also use for "what should I work on next to win" questions about this project.
---

# Production-Ready Submission for noob-agent

Goal: win **Most Production-Ready** at CoreWeave Hacks Part 2. That means F1
tickets, $1k and a stage demo at Fully Connected. Part 2 has **no live
judging demos**: judges only review what is submitted on the AGI House Part 2
page. The submission (repo, README, video, W&B links) *is* the demo. Every
hour should go toward making a judge who spends 5 minutes on it believe
"I could run this in production tomorrow."

## Hard rules (disqualification or repo-rule violations)

These come before everything else. Check them at the start of every session:

1. **Expansion, not a new project.** Every change must visibly extend the
   Part 1 noob-agent submission (learning loop, Doom and Minecraft connectors,
   Weave tracing). Never start a new repo or pivot the idea.
2. **Repo is public on GitHub.** Run `gh repo view NWelde/noob-agent --json visibility`.
3. **W&B is visibly used.** Judges must see traces or something else in the
   W&B UI. The Weave project and any W&B Report link must be **public**. Check
   this in a logged-out/incognito browser before submitting. Ask the user to
   do it, because you cannot.
4. **Submit to the Part 2 AGI House page**, not the Part 1 page.
5. **Deadline: "midnight (12am) PT September 30".** This is ambiguous. Treat
   **2026-09-29 23:59 PT** as the real deadline and have the submission done
   by the 29th. Remind the user whenever you plan the schedule.
6. **Someone attends the ceremony**: 2026-10-01 at 4:50 PM (arrive 4:40), Moscone South,
   Expo Stage, or by Zoom. The user must have registered for Fully Connected
   with the promo code (ashive@coreweave.com / alexa@agihouse.org if missing).
7. **Repo workflow still applies** (`CLAUDE.md`, `AGENTS.md`): feature branch
   in a worktree off `origin/main`, `hackathon_plan.md` is the source of
   truth, test first, one file per commit, `CHANGELOG.md` entry, PR never
   self-merged. Any change to protected core-loop code, dependencies or CI
   needs an **approved plan section first**. Hardening work is core-loop work
   more often than it looks.
8. **Honesty beats polish.** Never present a replay as live, a fixture skill
   (`scripted-fixture-not-model-generated`) as model-generated, or a single
   lucky run as the typical result. Every number in the README, video or
   submission text must trace to a named run ID or scorecard. A production-ready
   judge rewards accurate "known limitations" and punishes overclaiming.

## Workflow

Run these phases in order. Skip a phase only when its exit check already passes.

### Phase 1: Audit (read-only, about 20 minutes)

1. `git fetch && gh pr list --state all --limit 20`. Merges land fast, so work
   from `origin/main`, not the local checkout.
2. Read `README.md`, `docs/current-status.md`, `docs/loop-optimization.md`,
   the latest `CHANGELOG.md` entries, and `hackathon_plan.md` sections 10–12 and
   the newest section.
3. Score the repo against [references/rubric.md](references/rubric.md). Give
   each row a status of green, yellow or red, the evidence (file, command or
   run ID), and the smallest fix.
4. Report the scorecard to the user as a table, followed by the top 5 gaps
   ranked by *judge impact ÷ hours*. Do not edit anything yet.

### Phase 2: Plan (needs the user's approval)

Draft a new `hackathon_plan.md` section, "Production-ready submission", on a
`docs/` branch. For each work item it should give the goal, the exact files,
the acceptance tests, and whether it is scaffolding or core-loop work. Order
the items by the days remaining (see *Schedule*). Open it as a PR and **wait
for approval** before building any core-loop item.

### Phase 3: Harden (one PR per item, stacked)

Build the approved items test-first. Favor the things judges can *see* when
they read the repo:

- **CI badge that is green**: a GitHub Actions workflow running `uv run pytest`,
  Ruff and mypy with no credentials. This is the single strongest signal and needs
  plan approval (CI is protected).
- **One-command reproducibility from a fresh clone**: prove it by actually
  cloning into the scratchpad and following the README word for word.
  Log every place you had to deviate, and fix each one.
- **Failure behavior**: preflight checks, clear error messages, retries and
  timeouts on model calls, graceful behavior when W&B/Weave is unreachable
  (SQLite stays authoritative), and bounded budgets. Show each one with a test.
- **Observability as a product feature**: a Weave trace per episode, the
  scorecard logged to W&B, and ideally a **public W&B Report** comparing
  cold and learned results with cost, latency and token numbers.
- **Security posture**: the generated-skill sandbox (CoreWeave Sandbox, or the
  clearly labeled local fallback), the static policy checks, and no secrets in
  the repo (`git log -p | grep -i "api_key\|secret"` sanity check, `.env.example`
  only).
- **Versioned release**: a `v1.0.0` tag and GitHub Release with notes the user
  creates after the final merge. Don't tag it yourself unless asked.

Stop and ask the user if an item fails twice or grows past 10 files.

### Phase 4: Demo video (the centerpiece)

Follow [references/demo-video.md](references/demo-video.md). The deliverable is a
≤3-minute recorded video with a shot list, narration script, and exact
commands. It must use real recorded runs labeled as such. Record from a
freshly-reset state with all windows pre-opened.

### Phase 5: Package and submit

Work through [references/submission-checklist.md](references/submission-checklist.md):
the README's first screen, the submission text, links, a fresh-clone test, the
public-link test, and ceremony prep. Hand the user a final go/no-go list. The
user presses submit, never you.

## Schedule (today's date decides the phase)

| Dates (2026) | Focus |
| --- | --- |
| Sep 18–21 | Audit, plan approval, CI and reproducibility fixes |
| Sep 22–25 | Hardening items and a W&B Report with fresh benchmark numbers |
| Sep 26 | **Feature freeze.** Only fixes and docs after this |
| Sep 27–28 | Record the video, finalize the README and submission text, run the fresh-clone test |
| Sep 29 | **Submit** (the safe deadline), then verify every link while logged out |
| Sep 30–Oct 1 | Ceremony prep: 60-second pitch, backup laptop and recording, arrive 4:40 PM |

When behind schedule, cut scope in this order: new game features, then more
benchmark runs, then extra hardening. Never cut the video, README, CI or the
public W&B links.

## Positioning (use this wording consistently)

- **One line:** "noob-agent is a drop-in harness that measures whether a model
  can turn experience in an unfamiliar game into tested, reusable code, and
  it does this in a way you could run in CI."
- **Why it is production-ready:** deterministic seeds; frozen budgets; SQLite as
  the source of truth with Weave mirroring; independent private grading;
  sandboxed and versioned skills; and an honest scorecard with cost.
- **Cross-game proof:** the same Action agent, Builder, registry and grader run on
  Doom and Minecraft. Only the connector changes.
- Lead with measured Doom numbers (for example, skills accepted in 5 of 5
  sequences, 0 of 597 unusable replies). **Refresh every number from the latest
  scorecard** before it goes anywhere public.
