"""A completed learning sequence has one durable, harness-only summary."""

from __future__ import annotations

from datetime import UTC, datetime

from noob_agent.domain.records import EpisodeRecord, ExperimentRecord, SequenceSummaryRecord
from noob_agent.storage import EpisodeStore


def test_sequence_summary_is_written_once_and_round_trips(
    store: EpisodeStore, experiment: ExperimentRecord, episode: EpisodeRecord
) -> None:
    store.create_experiment(experiment)
    store.create_episode(episode)
    summary = SequenceSummaryRecord(
        sequence_id="doom-demo-s01",
        run_kind="non-benchmark-token-budget-demo",
        training_episode_id=episode.episode_id,
        training_goal_completed=True,
        builder_stop_reason="unusable_reply",
        builder_truncated=True,
        accepted_skill_name=None,
        accepted_skill_version=None,
        heldout_total=6,
        heldout_completed=0,
        heldout_skipped_reason="unusable_reply",
        input_tokens=1_234,
        output_tokens=32_000,
        finished_at=datetime(2026, 9, 13, tzinfo=UTC),
    )

    store.record_sequence_summary(summary)

    assert store.read_sequence_summary(summary.sequence_id) == summary
