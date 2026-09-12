# Repository Guidelines

## Non-Negotiable Agent Workflow

These rules apply to every coding agent working in this repository.

- `hackathon_plan.md` is the source of truth. Read it before beginning work and do not deviate from it. If it is missing, incomplete, or conflicts with a request, stop and ask for an updated or scoped plan before changing code.
- Do not begin an unscoped feature. Ask the requester to define the feature's boundaries, expected behavior, and acceptance criteria first.
- Follow test-driven development: write a failing test that describes the intended behavior before writing or changing production code. Do not write implementation code first.
- Keep `CHANGELOG.md` current. Every code change requires a corresponding changelog entry made in the same change set.
- Stage and commit files explicitly, one file at a time (for example, `git add path/to/file`). Never use `git add .`, `git add -A`, or other bulk staging commands.
- Keep commits focused and use concise imperative commit subjects.
- Before editing, inspect the relevant implementation, tests, and public interfaces. Do not guess at APIs, architecture, or existing behavior.
- Stop and ask for direction if requirements conflict, an operation would delete or overwrite data, an error persists after two focused attempts, or the change affects authentication, payments, secrets, production configuration, deployments, infrastructure, database/schema migrations, or CI.
- Do not add, upgrade, or remove dependencies without explicit approval. Do not use destructive or forceful Git commands without explicit approval of the exact command and target.
- Keep each pull request to one feature or fix. Do not mix unrelated refactors into it. Changes spanning more than 10 files require explicit approval before implementation.
- Every pull request must state the purpose, files changed, tests added, automated-check results, manual verification steps, known limitations, and rollback approach.

## Changelog Format

Use this exact format for each entry in `CHANGELOG.md`:

`YYYY-MM-DD HH:MM TZ | [ADDED] | Brief description`

Allowed tags are `[ADDED]`, `[FIXED]`, and `[DOCUMENTED]`. Use the actual local date, time, and timezone when recording the entry. Add new entries at the top of the `Unreleased` section.

## Repository Protection

Configure these protections in the GitHub repository settings; instruction files do not replace access controls:

- Protect `main`: require pull requests, at least one approving review, passing test/lint/type-check/build checks, resolved conversations, and up-to-date branches; prohibit force pushes and direct pushes.
- Do not allow an author to approve their own pull request or merge their own changes when another reviewer is required.
- Restrict write access to production deployment credentials and sensitive configuration. Use a `CODEOWNERS` file for CI, infrastructure, credentials, database/schema, connector, grader, and agent-runtime paths once their GitHub owner/team identifiers are known.

## Project Structure & Module Organization

This repository is currently an empty Git scaffold: no source, test, asset, or configuration directories have been added yet. As the project grows, keep production code under `src/`, tests under `tests/` (or alongside modules when the chosen framework expects co-located tests), and static assets under `assets/`. Group modules by feature or domain rather than by file type. Keep scripts and one-off maintenance tooling in `scripts/`.

## Build, Test, and Development Commands

No build system or package manifest is present, so there are no repository-specific commands yet. When adding tooling, document the canonical commands in `README.md` and here. Prefer predictable entry points such as:

- `npm run dev` — start the local development server.
- `npm run build` — create a production build.
- `npm test` — run the full automated test suite.
- `npm run lint` — check formatting and static-analysis rules.

Use the project’s actual package manager and lockfile consistently (for example, `pnpm` with `pnpm-lock.yaml`); do not mix dependency managers.

## Coding Style & Naming Conventions

Use the formatter and linter adopted by the project, and run them before opening a pull request. Use two spaces for indentation unless a language-specific formatter dictates otherwise. Name files and directories consistently with their ecosystem; prefer `kebab-case` for general-purpose files, `PascalCase` for exported UI components or classes, and `camelCase` for variables and functions. Keep modules small, explicit, and focused on one responsibility.

## Testing Guidelines

Add tests for new behavior and regressions. Name tests after the behavior they verify, such as `creates_user_test` or `creates-user.test.ts`, following the selected framework’s convention. Keep unit tests fast and deterministic; isolate integration or end-to-end tests and document any required services or environment variables. Add coverage thresholds once a test framework is introduced.

## Commit & Pull Request Guidelines

There is no commit history yet, so no existing message convention can be inferred. Use concise imperative subjects (for example, `Add initial project structure`) and keep each commit focused. Pull requests should explain the motivation, summarize implementation details, list validation commands and results, link related issues, and include screenshots or recordings for user-facing changes.

## Security & Configuration

Never commit secrets, private keys, or local environment files. Provide a safe `.env.example` for required configuration, keep real values in local or hosted secret storage, and update documentation when configuration changes.
