"""SQLite schema for the local source of truth.

Public payloads (manifest, observations, requests, results) are stored as JSON
text exactly as delivered, so a stored episode can be replayed byte-for-byte.
Scalar columns hold only the bookkeeping the harness queries on.
"""

from __future__ import annotations

SCHEMA_VERSION = 1

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
    """
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER NOT NULL
    )
    """,
)
