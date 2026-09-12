# Skill Version Registry

`registry.py` is the local record of every generated skill candidate and the
only place that decides which version an Action agent may be offered. It stores
candidates; it does not create, validate, or execute them.

## Data flow

```text
SkillPackage (source + declared metadata)
  └─ SkillRegistry.propose() → SkillVersion (version, content_hash assigned here)

proposed ──▶ validating ──▶ accepted ──▶ retired
                   └──────▶ rejected ──▶ (repair proposes a new version)
```

## Records

- `SkillPackage` is one submitted candidate: `name`, `source`, an opaque JSON
  `metadata` object, and `api_version`.
- `SkillVersion` is one immutable registry entry: the package, the assigned
  `version` and `content_hash`, the `parent_version` a repair was written from,
  the authoring episode and model, the `status`, and its `status_reason`.

## Rules

- The registry, not the Builder, assigns the version number and content hash.
  The hash sorts keys, so the same content always fingerprints identically and
  any content change requires a new evaluation run.
- Recorded content is never edited. A repair calls `propose()` with
  `parent_version` set to the rejected version it follows; the rejected record
  stays exactly as it was.
- Only the transitions drawn above are permitted. Every other move raises
  `IllegalTransitionError`.
- At most one version of a skill is `accepted` at a time. Accepting another
  raises `ConflictingAcceptedSkillError` until the live one is retired.
- `available_skills()` and `accepted_skill()` return accepted versions only.
  A proposed, validating, rejected, or retired candidate is never exposed,
  because showing one would contaminate the held-out evaluation.

## Deferred responsibilities

- Package checks, typed `noob-agent.skill.v1` metadata models, and AST static
  policy checks belong to the validation boundary. The registry records the
  metadata it is handed and does not interpret or trust it.
- Durable storage. Versions currently live in process; persisting them through
  the SQLite source of truth is a separate milestone.
- Status history. A version keeps its current status and reason, not the full
  audit trail of every transition it passed through.
- The one-repair-per-rejection policy is the Builder loop's to enforce; the
  registry only requires that a repair's parent is a rejected version.
