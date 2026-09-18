# Demo Video

The video is the only "demo" judges see in Part 2. Make it at most 3 minutes, at 1080p,
with narration or captions, uploaded unlisted to YouTube or Loom, and linked at
the top of the README and in the submission.

## Before recording

1. Pick the run to show from real, recorded runs. Prefer a sequence whose
   result is *representative*. If the most visual run is the best one, say so
   on screen ("best of 5; median shown later").
2. Have ready: a recorded Doom learning sequence (`scripts/replay_doom_episode.py`),
   its Weave trace URL, the live reasoning view (`scripts/live_reasoning_view.py`),
   the scorecard or W&B Report, and the GitHub repo with a green CI badge.
3. Label every replay on screen: "Recorded run <run-id>, replayed."
4. Close unrelated windows, hide secrets and `.env`, and enlarge the terminal font.
5. Write the narration script first and time it aloud.

## Shot list (adapted from plan section 12 for a recorded, judge-only video)

| Time | Shot | Say |
| --- | --- | --- |
| 0:00–0:15 | Title card, then the Doom window | The question: can a model turn experience in an unfamiliar game into reliable software? |
| 0:15–0:40 | Cold attempt replay with its tool list (primitives only) | It starts as a noob with primitives only, and fails or struggles within a fixed budget |
| 0:40–1:10 | The Weave trace of the cold attempt, then the Builder's skill code, then the validation checks | The Builder writes Python from the public trace, and the sandbox validates it |
| 1:10–1:40 | Held-out replay: a new seed, a cleared conversation, and the tool list now including the skill | Transfer to an unseen variation, graded by an independent grader |
| 1:40–2:05 | Minecraft, the same loop with a different connector | Only the connector changed. State what is and is not proven there |
| 2:05–2:40 | "Production-ready" montage: the CI badge, a one-command quickstart, the test count, failure handling, cost per episode in W&B | Why you could run this for real |
| 2:40–3:00 | The W&B Report scorecard, then the closing line | The measured result, the limitations in one sentence, and the repo link |

## Honesty checklist (all must be yes)

- [ ] Every replay is labeled, and no fixture skill is shown as model output
- [ ] Every number on screen matches a cited run or scorecard
- [ ] Limitations are said out loud once
- [ ] The W&B UI appears on screen for at least 20 seconds

Claude cannot record video. Produce the script, shot list, exact commands and
window layout, then give recording to the user.
