from noob_agent.redstone.repair_guard import RepairGuard


def test_blocks_same_failed_checks_with_unchanged_layout() -> None:
    guard = RepairGuard()
    failures = [{"check": "5:load", "expected": {"a": 1}, "actual": {"a": 0}}]
    declaration = {"module": "register", "controls": ["step"]}
    guard.record("register", failures, "layout-a", declaration)

    assert guard.blocks_regrade("register", failures, "layout-a", declaration)


def test_allows_regrade_after_verified_layout_change() -> None:
    guard = RepairGuard()
    failures = [{"check": "5:load", "expected": {"a": 1}, "actual": {"a": 0}}]
    declaration = {"module": "register", "controls": ["step"]}
    guard.record("register", failures, "layout-a", declaration)

    assert not guard.blocks_regrade("register", failures, "layout-b", declaration)


def test_allows_regrade_after_failure_signature_changes() -> None:
    guard = RepairGuard()
    declaration = {"module": "register", "controls": ["step"]}
    guard.record("register", [{"check": "5:load", "actual": {"a": 0}}], "layout-a", declaration)

    assert not guard.blocks_regrade(
        "register", [{"check": "6:load", "actual": {"a": 0}}], "layout-a", declaration
    )


def test_serialized_state_preserves_guard_decision() -> None:
    guard = RepairGuard()
    failures = [{"check": "5:load", "expected": {"a": 1}, "actual": {"a": 0}}]
    declaration = {"module": "register", "controls": ["step"]}
    guard.record("register", failures, "layout-a", declaration)

    restored = RepairGuard.from_state(guard.to_state())

    assert restored.blocks_regrade("register", failures, "layout-a", declaration)
    assert not restored.blocks_regrade("register", failures, "layout-b", declaration)


def test_allows_regrade_when_declaration_changes() -> None:
    guard = RepairGuard()
    failures = [{"check": "5:load", "expected": {"a": 1}, "actual": {"a": 0}}]
    guard.record("register", failures, "layout-a", {"recipe": "old"})

    assert not guard.blocks_regrade("register", failures, "layout-a", {"recipe": "new"})


def test_prior_failure_signature_remains_distinguishable() -> None:
    guard = RepairGuard()
    old_failures = [{"check": "5:load", "expected": {"a": 1}, "actual": {"a": 0}}]
    current_failures = [{"check": "6:load", "expected": {"a": 1}, "actual": {"a": 0}}]
    declaration = {"module": "register"}
    guard.record("register", old_failures, "layout-a", declaration)

    assert not guard.blocks_regrade("register", current_failures, "layout-a", declaration)


def test_declaration_fingerprint_round_trips() -> None:
    guard = RepairGuard()
    failures = [{"check": "5:load", "expected": {"a": 1}, "actual": {"a": 0}}]
    guard.record("register", failures, "layout-a", {"recipe": "same"})

    restored = RepairGuard.from_state(guard.to_state())

    assert restored.blocks_regrade("register", failures, "layout-a", {"recipe": "same"})
    assert not restored.blocks_regrade("register", failures, "layout-a", {"recipe": "changed"})


def test_detects_cell_behavior_cycle_and_reports_history_evidence_needed() -> None:
    guard = RepairGuard()
    failed = {"path": "input->torch", "expected": 1}
    guard.record_observed_mutation("m", "26", "torch-wire", "A", "B", failed)
    guard.record_observed_mutation("m", "26", "torch-wire", "B", "A", failed)

    assert guard.blocks_repeated_repair("m", "26", "torch-wire", failed)
    feedback = guard.repair_feedback("m", "26", "torch-wire", failed)
    assert "Cell 26" in feedback
    assert "A→B→A" in feedback
    assert "explicit targeted readback" in feedback


def test_repeated_no_progress_edits_trip_threshold_but_unrelated_edits_do_not_clear() -> None:
    guard = RepairGuard(no_progress_threshold=2)
    failed = {"control": "step", "path": "torch input"}
    guard.record_observed_mutation("m", "26", "torch-wire", "off", "off", failed)
    guard.record_observed_mutation("m", "27", "torch-wire", "stone-a", "stone-b", failed)
    guard.record_observed_mutation("m", "26", "torch-wire", "off", "off", failed)

    assert guard.blocks_repeated_repair("m", "26", "torch-wire", failed)
    assert guard.blocks_repeated_repair("m", "27", "torch-wire", failed) is False


def test_only_new_explicit_targeted_diagnostic_relieves_behavioral_block() -> None:
    guard = RepairGuard()
    failed = {"path": "input->torch"}
    guard.record_observed_mutation("m", "26", "sig", "A", "B", failed)
    guard.record_observed_mutation("m", "26", "sig", "B", "A", failed)

    guard.record_targeted_diagnostic(
        "m", "26", "sig", failed, {"wire": "same"}, explicit_request=False
    )
    assert guard.blocks_repeated_repair("m", "26", "sig", failed)
    guard.record_targeted_diagnostic(
        "m", "26", "sig", failed, {"wire": "old"}, explicit_request=True
    )
    assert not guard.blocks_repeated_repair("m", "26", "sig", failed)
    # Once relieved, a duplicate readback does not create or change guard state.
    guard.record_targeted_diagnostic(
        "m", "26", "sig", failed, {"wire": "old"}, explicit_request=True
    )
    assert not guard.blocks_repeated_repair("m", "26", "sig", failed)
    guard.record_targeted_diagnostic(
        "m", "26", "sig", failed, {"wire": "new", "powered": False}, explicit_request=True
    )
    assert not guard.blocks_repeated_repair("m", "26", "sig", failed)


def test_incomplete_or_unknown_mutations_are_ignored() -> None:
    guard = RepairGuard()
    failed = {"path": "p"}
    guard.record_observed_mutation("m", "26", "sig", "A", "B", failed, completed=False)
    guard.record_observed_mutation("m", "26", "sig", "A", "B", failed, observed_success=False)
    assert not guard.blocks_repeated_repair("m", "26", "sig", failed)


def test_behavioral_cycle_persists_across_state_round_trip() -> None:
    guard = RepairGuard()
    failed = {"path": "p"}
    guard.record_observed_mutation("m", "26", "sig", "A", "B", failed)
    guard.record_observed_mutation("m", "26", "sig", "B", "A", failed)
    restored = RepairGuard.from_state(guard.to_state())

    assert restored.blocks_repeated_repair("m", "26", "sig", failed)
    assert restored.repair_feedback("m", "26", "sig", failed)
