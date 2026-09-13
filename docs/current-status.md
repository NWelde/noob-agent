# Current Implementation Status

This is a dated handoff for `main` at commit `f86f76e` on 2026-09-12. The
authoritative scope and build order remain in
[`hackathon_plan.md`](../hackathon_plan.md); this document records what is
actually present and which live environment work remains.

## Present on `main`

| Area | Current implementation |
| --- | --- |
| Foundation | Python package, CLI placeholder, settings seams, typed public contracts, and deterministic fake connector |
| Storage and runs | SQLite source of truth, durable experiment/episode/step/outcome records, schema-v2 finding/verdict/reproduction records, bounded cold and held-out runners, and best-effort nested Weave mirroring |
| Skills | Package/schema validation, static policy checks, immutable versions, accepted-only discovery, nested primitive accounting, and a labeled local-subprocess executor |
| Builder | Provider-neutral model client with disabled default, W&B Inference adapter, bounded public evidence selection, candidate authoring, one-version-at-a-time repair lineage, and finite repair budget |
| Action and held-out | Fixed Action prompt, fresh conversation per held-out episode, accepted-skill reuse, frozen budgets, and no held-out path back to Builder or validation |
| Grading | Strict public finding records, private matched-pair verification, clean-twin control, fresh-seed reproduction, and first-mismatch recording |
| Minecraft | Vanilla 1.21.1 Resonator training data pack, deterministic reset/oracle, and a Mineflayer JSON Lines connector implementing reset and observe |
| Doom | ViZDoom 1.3.0, all 10 frozen BDP primitives, fixed-seed reset, public HUD/visible-label observations, and live connector tests |

The Minecraft server is outside Git at
`/home/nathan/.local/share/noob-agent/minecraft-server`. The deployed world is
`noob-agent-training`, the server uses port `25566`, and the operational guide
is [`minecraft-server.md`](minecraft-server.md).

## Recent verified developments

- The dedicated Minecraft server, world, Java 21 runtime, and pinned Mineflayer
  dependency are installed.
- The server runs in tmux session `noob-agent-minecraft`; `noobagentbot` is the
  only operator and both `noobagentbot` and `nathanbeyene` are allowlisted.
- A vanilla TLauncher 1.21.1 client connected from Windows through the current
  WSL IP. `localhost` forwarding did not work on this machine.
- The human spawn was moved into the Resonator room to avoid the natural
  spawn's powder snow.
- `tests/test_connectors_minecraft.py` passed all 24 tests against the live
  server, including repeatable reset and a real observation action.
- ViZDoom 1.3.0 is installed locally; the headless Doom environment check
  passed all 26 primitive, reset, timing, and public-observation checks.
- Build-order step 4, the Builder-generated candidate, is on `main` through PR
  #27 and the steps 4-6 scope approval is on `main` through PR #26.
- The rejected-request sequence fix landed through PR #30, so a rejected
  Minecraft request is durably recorded without falsely ending the episode as
  an unknown result.
- Build-order step 5 landed on `main` through integration PR #31. The combined
  tree passed 222 tests, and a live held-out run offered one accepted
  Builder-produced skill, completed all 12 uses, passed all 12 public success
  checks, and recorded all 12 nested Minecraft primitives.
- PR #29 was retargeted directly to `main`, synchronized after step 5, reduced
  to its intended 10-file step-6 diff, and merged. The final tree passed all
  248 tests, the 26 focused grader/reproduction tests, Ruff, and strict mypy.
- Concurrent PR #32 added the step-7 headless ViZDoom connector and declared
  ViZDoom as a runtime dependency. It landed after PR #29 and was pulled into
  this local `main` before the handoff was finalized; the resulting full tree
  passed all 251 tests, Ruff, and strict mypy.

## Doom learning sequence (build-order step 8)

Section 17 of `hackathon_plan.md` approved step 8, which was built in three
parts:

- **8a (PR #37):** connector `doom-vizdoom-v2`, with declared training and
  held-out scenarios, precommitted seeds, a `screen_offset` aiming signal, and a
  harness-only private outcome.
- **8b (PR #38):** the independent Doom grader.
- **PR #39:** unique episode IDs across connector instances.
- **8c:** `LearningSequence` in `src/noob_agent/runtime/sequence.py` and
  `scripts/run_doom_learning_sequence.py`.

With real ViZDoom and a scripted provider, the plumbing works end to end. The
provider returns a hand-written fixture skill, labeled
`scripted-fixture-not-model-generated`, which is never a model result. The
fixture was accepted and offered in `doom-basic-heldout-a` and
`doom-basic-heldout-b`. It was invoked, every nested primitive was charged, and
both held-out episodes graded as success.

### Live run, 2026-09-12 22:07 PDT: no skill learned

Command:

```sh
NOOB_AGENT_SANDBOX_MODE=local uv run --env-file .env python scripts/run_doom_learning_sequence.py
```

The model was `deepseek-ai/DeepSeek-V4-Flash-0731` through W&B Inference. The
executor was the labeled local subprocess, which is weaker isolation than
CoreWeave Sandbox. The sequence was `doom-seq-20260913T050753Z`, recorded in the
Git-ignored `.noob-agent/doom-learning-live.sqlite3`.

| Phase | Result |
| --- | --- |
| Cold training (`doom-basic-training`, seed 20260912) | `decision_limit`: 20 decisions, 7 primitives, goal not completed |
| Builder | Not accepted, `unusable_reply`, 1 attempt, 774 input and 2,048 output tokens |
| Held-out (6 cells) | Skipped because no skill was accepted |

What the stored training episode shows:

- 13 of the 20 Action replies could not be parsed as a decision. Each was
  recorded as a rejected `unusable_reply` that cost a decision but no primitive.
- The 7 parsed decisions were all `attack`. The model never turned, although the
  target stayed at `screen_offset` 38, right of center. No shot killed it, and
  health stayed at 100 with 35 ammunition remaining.
- A transient `BulletPuff` label from the model's own shots appeared as a
  visible object.
- The Builder's output used exactly its 2,048-token cap, so the reply was
  probably cut off before its closing code block. The Action agent's cap is 512
  tokens.

Model responses are not persisted, so this record cannot show why the replies
were unusable. BDP criteria 3 and 5-8 for Doom are therefore not met by a live
model yet.

## Integrated pull-request state

- PR #30: merged connector rejected-sequence prerequisite.
- PR #31: merged step-5 integration into `main`.
- PR #29: retargeted to `main` and merged step 6 after step 5.
- PR #32: merged the Doom connector for build-order step 7.
- The former stacked branch topology no longer prevents steps 5-6 from being
  present on `main`.

## Next steps

1. Obtain a scoped plan update for the remaining live Minecraft grading work:
   section 14 did not authorize changing the connector for a grader-only
   private-state operation.
2. Implement and freeze the Minecraft validation, held-out-clean, and
   held-out-faulty snapshots. Add the grader-only path for reading private
   predicates without leaking them to Action or Builder prompts, then run one
   end-to-end clean/faulty reproduction.
3. Close or update issues #22-#24 so issue state matches merged code and manual
   acceptance, rather than stacked PR bookkeeping.
4. Make live Doom learning succeed. First persist or trace the raw model
   replies, so the unusable Action and Builder replies can be diagnosed. Then
   decide, through a scoped plan update, whether the output-token caps, the
   reply format, or the model choice should change. The approved plan holds the
   prompts and budgets fixed, so none of these may change without that update.
5. Obtain a new scoped plan approval before step 9. The current approval
   explicitly excludes the comparison runner and report-generation path.

The immediate priority is the live Minecraft clean/faulty environment and
private grading path, not new sponsor extensions or a Minecraft mod.
