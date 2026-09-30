# Redstone harness improvements

The September 29 computer trial exhausted its loop after seven sessions and
1,949 actions. Its completed register evidence passed zero load, zero hold,
and reset; loading one observed zero. It did not pass a register checkpoint or
a full computer evaluation. These changes address harness overhead and stalled
construction without supplying the agent a circuit design or changing the model.

Construction actions are admitted with conservative transport, reconnect, and
verification costs before mutation. An admitted action may finish its readback
past the local intention deadline; the session wall limit and primitive limit
remain hard limits. A deferred action returns to planning without optional
grading. The manifest records this as `action-boundary-v1`, so its timing must
not be compared directly with the earlier policy.

Action offers carry dependencies. The scheduler orders replacement breaks,
support placement, attachments, and removal of supported hardware, using the
verified layout. Only verified effects satisfy a dependency. Failed work remains
visible in the next planner feedback. Identical verified placements are skipped,
while break-and-replace sequences retain their actions.

The planner can preserve its own circuit plan, recent feedback, and compact
verified layout across sessions. Plans describe intended circuitry; they do not
count as world or behavioral evidence. Repair guards retain repeated hardware
cycles and behavioral failures, and require a fresh explicit diagnostic before
retrying blocked work.

Every event attempt and result is appended to a flushed, fsynced JSONL journal.
Atomic manifest snapshots are periodic and terminal. Readers use
`TrialManifest.load_data()` to replay the journal; reading the snapshot alone
can miss recent events. Recovery rejects completed malformed records and
sequence gaps, while ignoring an incomplete final record left by interruption.

The optional [register workshop](register-workshop.md) supplies a support pad
and staged one-bit diagnostics. It supplies no circuit. One-bit results cannot
award a register checkpoint or full computer success; those still require the
existing complete behavioral suites. Workshop setup is separately recorded and
is not baseline comparable.

Unit and transport fixtures verify harness behavior. They do not establish that
the live agent has built a working register. A live continuation remains the
necessary evidence for that claim.
