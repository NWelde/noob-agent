# Claude Code Instructions

Claude Code must follow all rules in [`AGENTS.md`](AGENTS.md). The following rules are especially strict:

1. Work only on a feature branch. Before modifying files, verify that the current branch is not `main`.
2. Never push to `main`, merge into `main`, or commit directly on `main`.
3. When work is ready, create or update a pull request from the feature branch for review; do not self-merge it.
4. Treat `hackathon_plan.md` as the source of truth. Do not begin or alter implementation when it is absent, ambiguous, or inconsistent with the requested work; ask the requester to resolve it.
5. For a feature request that is not clearly scoped, ask the requester to provide scope and acceptance criteria before changing files.
6. Write the test first, confirm it fails for the expected reason, and only then write production code.
7. Update `CHANGELOG.md` whenever code changes, using the format and allowed tags defined in `AGENTS.md`.
8. Stage and commit one explicit file at a time. Never use bulk staging commands such as `git add .` or `git add -A`.

## Default Permission: Isolated Scaffolding Only

Claude Code's default role is to build isolated scaffolding, not the noob-agent main agent loop. It may create or edit only documentation and files under `scaffolding/` unless the requester explicitly approves a plan that names the core files or interfaces to change.

Scaffolding is disposable, non-production support work that is not imported, called, built, or deployed by the main application. Suitable scaffolding for this project includes:

- mock `GameConnector` observations and primitive-tool responses;
- test fixtures, test helpers, and sample episode/skill manifests;
- contract-validation examples derived from `connector_contract.md` and `skill_contract.md`;
- local developer documentation, diagrams, and setup checklists;
- prototype scripts that run only from `scaffolding/` and do not access credentials, a live game server, W&B, or production configuration.

Scaffolding must remain isolated: it cannot be imported by production code, modify shared runtime configuration, add dependencies, start services, access secrets, or change CI. It must include a short README explaining how to run it and how to remove it.

## First Authorized Task: Deterministic Fake-Connector Scaffolding

Claude Code's first task is expressly authorized as isolated scaffolding. Work
in a separate worktree on a non-`main` feature branch, so its changes can run
in parallel with core contract work.

Create `scaffolding/fake_connector/` only, plus the required `CHANGELOG.md`
entry. Do not edit `src/`, `tests/`, shared configuration, dependencies, CI,
or any protected core-loop interface.

The scaffold must provide:

- a deterministic, standalone scripted fake-game transcript for `reset`,
  `observe`, one successful primitive action, one rejected invalid-tool action,
  and one terminal observation;
- JSON fixtures shaped exactly like the public examples in
  `connector_contract.md`, with no imports from production code;
- a small validator or test script that verifies sequence progression,
  action-ID matching, result statuses, and the absence of private fields such
  as clean/faulty labels, grader state, or hidden mechanic answers; and
- a README that gives the run command, labels the scaffold non-production, and
  explains how to remove it.

Write the scaffold test first and confirm it fails for the expected reason
before implementation. Run its validation command after implementation. The
changelog entry must use the repository's required `[ADDED]` format. The pull
request must report the initial test failure, final automated-check results,
manual verification, known limitations, and rollback steps.

The protected main agent loop includes the Action agent, generated-skill runtime and registry, game connectors, scenario reset/action path, private grader, evaluation harness, tracing, and report-generation path. Claude Code must not edit these components—or their interfaces, schemas, dependencies, CI, credentials, deployment, or database/configuration—without an explicitly approved `hackathon_plan.md` section naming the intended change and acceptance tests.

## Mandatory Change Gate

Before modifying any file, Claude Code must provide a concise change proposal containing the goal, the exact files to change, tests to write first, validation commands, and whether the work is scaffolding or core-loop work. For core-loop work, it must wait for explicit approval after presenting the proposal.

For each pull request, Claude Code must include: purpose, files changed, tests added and their initial failure, test/lint/type-check/build results, manual verification, known limitations, and rollback steps. It must stop rather than continue if it encounters two unsuccessful focused attempts or any protected-area change.
