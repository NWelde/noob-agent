"""SQLite schema for the local source of truth.

Public payloads (manifest, observations, requests, results) are stored as JSON
text exactly as delivered, so a stored episode can be replayed byte-for-byte.
Scalar columns hold only the bookkeeping the harness queries on.

Version 2 adds the finding, finding_verdict, and reproduction tables. The
change is additive: a version-1 database gains the new tables on open and its
recorded episodes are untouched.
"""

from __future__ import annotations

SCHEMA_VERSION = 2

SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS experiment (
        experiment_id       TEXT    PRIMARY KEY,
        model_id            TEXT    NOT NULL,
        condition           TEXT    NOT NULL,
        connector_version   TEXT    NOT NULL,
        decision_budget     INTEGER NOT NULL,
        primitive_budget    INTEGER NOT NULL,
        wall_time_budget_ms INTEGER NOT NULL,
        created_at          TEXT    NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS episode (
        episode_id             TEXT    PRIMARY KEY,
        experiment_id          TEXT    NOT NULL REFERENCES experiment (experiment_id),
        game_id                TEXT    NOT NULL,
        scenario_id            TEXT    NOT NULL,
        seed                   INTEGER NOT NULL,
        split                  TEXT    NOT NULL,
        manifest_json          TEXT    NOT NULL,
        manifest_hash          TEXT    NOT NULL,
        reset_observation_json TEXT    NOT NULL,
        started_at             TEXT    NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS step (
        episode_id   TEXT    NOT NULL REFERENCES episode (episode_id),
        sequence     INTEGER NOT NULL,
        action_id    TEXT    NOT NULL,
        request_json TEXT    NOT NULL,
        result_json  TEXT    NOT NULL,
        PRIMARY KEY (episode_id, sequence)
    )
    """,
    # Action IDs are episode-local, so uniqueness is scoped to the episode.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS step_action_id
        ON step (episode_id, action_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS episode_outcome (
        episode_id       TEXT    PRIMARY KEY REFERENCES episode (episode_id),
        stop_reason      TEXT    NOT NULL,
        terminal         INTEGER NOT NULL,
        total_decisions  INTEGER NOT NULL,
        total_primitives INTEGER NOT NULL,
        finished_at      TEXT    NOT NULL
    )
    """,
    # A finding is the public report; its verdict and reproductions are the
    # private grader's records and are never read back into a prompt.
    """
    CREATE TABLE IF NOT EXISTS finding (
        finding_id         TEXT PRIMARY KEY,
        episode_id         TEXT NOT NULL REFERENCES episode (episode_id),
        action_id          TEXT NOT NULL,
        reporting_model_id TEXT NOT NULL,
        report_json        TEXT NOT NULL,
        reported_at        TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS finding_verdict (
        finding_id     TEXT PRIMARY KEY REFERENCES finding (finding_id),
        verification   TEXT NOT NULL,
        reason_code    TEXT NOT NULL,
        reason         TEXT NOT NULL,
        grader_version TEXT NOT NULL,
        decided_at     TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS reproduction (
        reproduction_id  TEXT    PRIMARY KEY,
        finding_id       TEXT    NOT NULL REFERENCES finding (finding_id),
        scenario_id      TEXT    NOT NULL,
        seed             INTEGER NOT NULL,
        build            TEXT    NOT NULL,
        attempted_json   TEXT    NOT NULL,
        result           TEXT    NOT NULL,
        predicate_result INTEGER,
        first_mismatch   TEXT,
        attempted_at     TEXT    NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER NOT NULL
    )
    """,
)
