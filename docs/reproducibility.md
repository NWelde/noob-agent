# Fresh-clone reproducibility log

This is the record for hackathon_plan.md section 26.5. It follows the
README's "Run the demo yourself" section word for word from a clean `git
clone`, on this reviewer's WSL2 Linux machine (Python 3.14.6, uv 0.11.15,
Java 21.0.12, Node 24.19.0), and lists every deviation found and its fix.

## Method

1. `git clone https://github.com/NWelde/noob-agent.git` into a scratch
   directory outside the repository (`fresh1`).
2. Followed README.md section "Run the demo yourself" verbatim: the install
   commands, `cp .env.demo.example .env`, `uv run pytest`, then the Doom and
   Minecraft demo commands up to (but not past) the point each one requires a
   real W&B API key or a downloaded Minecraft server.
3. No W&B credentials were available or used. No paid model calls were made.
   `scripts/setup_minecraft_server.py` was only run with `--help`, since it
   has no dry-run flag and would otherwise download a server jar.
4. After fixing the deviations below, re-cloned from the pushed
   `docs/26-5-fresh-clone` branch into a second scratch directory (`fresh2`)
   and repeated the same steps to confirm no deviation remained.

## Deviations found and their fixes

1. **Confusing error: `uv run pytest` fails with no explanation.**
   The README's install-check step (`uv run pytest`) reports 3 failing tests
   on a fresh clone, with nothing in the README or `docs/` telling a new user
   this is expected and tracked, not an install problem. It also reports 2
   more skipped tests than the audited baseline in `hackathon_plan.md`
   (5 skipped vs. 3), because the Node sidecar's dependencies are not
   installed yet on a fresh clone (they are installed later, by
   `scripts/setup_minecraft_server.py`).
   **Fix (`[DOCUMENTED]`):** added a paragraph to README.md's "Install"
   section naming the 3 known-failing tests, pointing to `hackathon_plan.md`
   section 26 for the tracked cause, and explaining the 2 extra skips.

2. **Confusing error: the Minecraft redstone demo crashes with a traceback
   and a blocking password prompt instead of a clear refusal.**
   `scripts/run_doom_learning_sequence.py` checks `WANDB_API_KEY` before
   doing anything else and prints a clear, actionable
   `Refusing to run: WANDB_API_KEY is not set. ...` message
   (exit code 2). `scripts/run_minecraft_redstone_demo.py` had no such
   check: with `.env` copied from `.env.demo.example` but no key filled in,
   running it interactively prompted for a W&B API key (Weave's own login
   flow) and, once that input was unavailable, crashed with an uncaught
   `WeaveWandbAuthenticationException` traceback. A new user following the
   README's Minecraft section with an empty key would hit this.
   **Fix (`[FIXED]`, `scripts/` preflight, not `src/noob_agent/`):**
   `scripts/run_minecraft_redstone_demo.py`'s `run()` now takes an optional
   `environ` mapping (for testing) and checks `settings.wandb.api_key`
   before building the trace sink, printing the same
   `Refusing to run: WANDB_API_KEY is not set. ...` message and returning
   exit code 2, before any Weave or network call. Its existing "configured
   model and Weave tracing are required" check was changed from a raised
   `RuntimeError` to the same printed-refusal-and-exit-2 style for
   consistency. `tests/test_minecraft_redstone_demo.py` gained a test that
   asserts this (written first; it failed with
   `TypeError: run() got an unexpected keyword argument 'environ'` before
   the fix, confirming the fix was necessary and the test exercised it).

## Known limitation not fixed here

`scripts/run_minecraft_easy_live_smoke.py`'s own `main()` (a separate entry
point not documented in the README's demo path) has the same missing
`WANDB_API_KEY` check as deviation 2 above. It was left as-is because it is
out of this step's scope (not part of the README-documented path) and
fixing it is not needed to make the README's documented commands
reproducible.

## Result

A second clean clone from `docs/26-5-fresh-clone` (see PR for URL) follows
the documented path with no deviation: `uv run pytest` reports the same 3
known-and-explained failures and 5 explained skips, and the Doom and
Minecraft demo commands fail with clear, actionable refusals when run
without credentials, matching what the README now says to expect.
