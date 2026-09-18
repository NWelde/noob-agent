# Go/no-go checklist

From `.claude/skills/production-ready-submission/references/submission-checklist.md`.
Run this on the 28th and again on the 29th, in a logged-out/incognito browser
for every public-link check. Status reflects this repo as of 2026-09-18.

| Item | Status | Who |
| --- | --- | --- |
| `origin/main` has every intended PR merged; no open PR is needed for the demo | **Not yet.** Open PRs still needed: [#76](https://github.com/NWelde/noob-agent/pull/76) (demo-trial budgets), [#81](https://github.com/NWelde/noob-agent/pull/81) (CI into `main`), [#82](https://github.com/NWelde/noob-agent/pull/82) (redstone repair-cap fix), [#83](https://github.com/NWelde/noob-agent/pull/83) (demo-trials doc), and this submission-package PR | Requester (review and merge) |
| A fresh clone into the scratchpad plus the README quickstart succeeds, and `uv run pytest` passes | **Yes (tests).** Confirmed reproducible in `docs/reproducibility.md`; with all section 26 PRs integrated, `uv run pytest -q` gives `674 passed, 5 skipped` | Agent (re-verify on `main` after the merge) — requester does the actual fresh clone |
| CI is green on the latest `main` commit | **Not yet** — `.github/workflows/ci.yml` only exists on [#81](https://github.com/NWelde/noob-agent/pull/81), not yet merged to `main` | Requester (merge #81, then check the Actions tab) |
| The repo is public; the Weave project and Report are public (checked logged out by the user) | Repo: public (confirmed per plan section 26 audit). Weave project `nathanweldegiorgis731-minerva-university/Noob-agent`: **visibility not yet confirmed public**. W&B Report: **does not exist yet** | Requester (make both public, verify logged out — an agent cannot check this) |
| The video plays logged out and is 3 minutes or shorter | **Not started** — see `docs/submission/video-script.md` for the shot list and commands | Requester (record, upload unlisted, verify) |
| No secrets: `.env` is untracked, and history has been grepped | `.env` is untracked (`.env.demo.example`/`.env.example` are the checked-in templates). A full `git log -p \| grep -i "api_key\|secret"` sweep of history has not been re-run as part of this task | Agent, on request — or requester before submitting |
| Every README number matches the latest scorecard | Done in this PR: README headline results cite `docs/loop-optimization.md` (`loop-bench-24b-r2`), `docs/demo-trials.md` (`doom-demo-trial-20260918c`), and the Minecraft trial 4 (`minecraft-redstone-20260918T122657Z`, attempt a02: skill accepted, invoked, and reused; held-out transfer still qualified as unproven) | Agent (done) |
| `CHANGELOG.md` is up to date; the release tag has been created by the user | Changelog entry added in this PR. **No release tag exists yet** | Requester (choose a version, tag, and push after `main` is ready) |
| Submitted to the **Part 2** page before 2026-09-29 23:59 PT, and the confirmation screenshot is saved | **Not submitted** | Requester |

## Requester-only actions this checklist surfaces

- Choose a license (no `LICENSE` file exists yet; add a license badge to the
  README once chosen).
- Make the Weave project and the W&B Report public, and verify both while
  logged out.
- Record the demo video per `docs/submission/video-script.md` and update the
  README's `_link coming_` placeholder.
- Merge the outstanding stacked PRs (#76, #81, #82, #83, and this one) in
  order, then re-run the reproducibility and CI checks against `main`.
- The Minecraft trial 4 result (`minecraft-redstone-20260918T122657Z`) is now
  filled into the README and `docs/submission/submission-text.md`; re-check
  it against `docs/demo-trials.md` once PR #83 lands on `main`, and update it
  again if a later trial supersedes it (for example, one that proves
  held-out transfer).
- Create the release tag and submit on the AGI House **Part 2** page (not
  Part 1) by 2026-09-29 23:59 PT.
- Confirm who attends the ceremony (2026-10-01, 4:50 PM, Moscone South Expo
  Stage, arrive 4:40 PM) — in person or by Zoom — and register for Fully
  Connected with the promo code if not already done.

## Ceremony prep

- [ ] A 60-second pitch using the same wording as the video (draft from
  `docs/submission/submission-text.md`'s one-liner and result numbers)
- [ ] The video downloaded locally in case venue Wi-Fi fails
- [ ] A 3-minute stage-demo plan using recorded runs only, no live model
  dependency (`hackathon_plan.md` section 11 reliability rules)
- [ ] A Zoom link as backup if nobody attends in person
