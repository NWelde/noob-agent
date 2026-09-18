# Submission Checklist

## README first screen (top of `README.md`, before "How it works")

1. The project name and the one-line pitch
2. Badges: CI, license, and optionally Python version
3. **Video link** (thumbnail image that links to the video)
4. **Headline results**: 3 bullets with numbers and run IDs
5. Links: the public Weave project, the public W&B Report, and the submission page
6. **What changed since Part 1**: 3–6 bullets, each linking to its PR
7. A quickstart of at most 3 commands, and a credential-free `uv run pytest`

## Submission text (draft for the user to paste into the AGI House Part 2 page)

- The title and one-liner
- Problem → approach → result (with numbers) → why it is production-ready
- The Part 1 → Part 2 delta
- Links: the repo, video, Weave and W&B Report
- W&B usage: exactly which products (Inference, Weave traces, Reports) and where to look
- Known limitations (2–4 honest bullets)
- The team members attending the ceremony, and whether in person or by Zoom

## Go/no-go checks (run on the 28th and again on the 29th)

- [ ] `origin/main` has every intended PR merged; no open PR is needed for the demo
- [ ] A fresh clone into the scratchpad plus the README quickstart succeeds, and `uv run pytest` passes
- [ ] CI is green on the latest `main` commit
- [ ] The repo is public; the Weave project and Report are public (checked logged out by the user)
- [ ] The video plays logged out and is 3 minutes or shorter
- [ ] No secrets: `.env` is untracked, and history has been grepped
- [ ] Every README number matches the latest scorecard
- [ ] `CHANGELOG.md` is up to date; the release tag has been created by the user
- [ ] Submitted to the **Part 2** page before 2026-09-29 23:59 PT, and the confirmation screenshot is saved

## Ceremony prep (Oct 1, 4:40 PM arrival, Moscone South Expo Stage)

- A 60-second pitch using the same wording as the video
- The video downloaded locally, in case the venue Wi-Fi fails
- A 3-minute stage-demo plan in case they win: recorded runs only, no live model dependency (plan section 11 reliability rules)
- A Zoom link as a backup if nobody can attend in person
