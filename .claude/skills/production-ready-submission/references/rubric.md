# Production-Ready Rubric

Score each row green, yellow or red, with evidence. Judges have minutes, not hours,
so weight is what a judge notices while skimming the repo, README and video.

| # | Area | Green means | Weight | How to check |
| --- | --- | --- | --- | --- |
| 1 | First impression | The README's first screen has a one-line pitch, the video link, a headline result with numbers, W&B links and a 3-command quickstart | High | Read the first 40 lines of `README.md` |
| 2 | Demo video | Under 3 minutes, real runs, shows W&B, ends on the result | High | See `demo-video.md` |
| 3 | W&B usage | Public Weave project with readable nested traces, plus a public W&B Report of the scorecard | High | Open links logged out |
| 4 | CI | A GitHub Actions badge is green on `main`, running tests, lint and type checks with no secrets | High | `ls .github/workflows`; `gh run list` |
| 5 | Reproducibility | A fresh clone plus the README quickstart works on the first try; pinned `uv.lock`; `.env.demo.example` | High | Clone into the scratchpad and follow it literally |
| 6 | Honest results | Every claim cites a run ID or scorecard; limitations are listed; replays are labeled | High | Grep the README for numbers and trace each one |
| 7 | Tests | The suite is fast, deterministic and credential-free, and the count is stated | Medium | `uv run pytest -q` |
| 8 | Failure handling | Preflight checks, timeouts, retries and bounded budgets; tracing failure does not lose data | Medium | Tests for each path |
| 9 | Security | Sandboxed skill execution (the local fallback is labeled), policy checks, no secrets in history | Medium | `skill_contract.md`, a history grep |
| 10 | Observability | Cost, tokens, latency and outcomes per episode are visible in Weave and the scorecard | Medium | Open one trace |
| 11 | Architecture docs | A diagram, contracts (`connector_contract.md`, `skill_contract.md`) and a repo map | Medium | README "How it works" |
| 12 | Extensibility | A documented "add a new game connector" path, proven by 2 games | Medium | Connector contract plus the fake connector |
| 13 | Release | A version tag, GitHub Release notes, a CHANGELOG and a license | Low–Med | `gh release list`; `LICENSE` |
| 14 | Part 1 → Part 2 delta | A clear "What changed since Part 1 (Sep 13)" section | High | The README or submission text |

Known gaps as of 2026-09-18 (verify, since they may be fixed): no
`.github/workflows`; the CoreWeave Sandbox executor is not built (local
subprocess only); the Minecraft full learning loop and clean/faulty
reproduction are unproven; held-out Doom transfer is uneven (8 of 30).
Decide with the user whether to *fix* each one or *state it plainly* as a
limitation. Both are acceptable. Hiding it is not.
