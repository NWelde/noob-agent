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
    guard.record(
        "register", [{"check": "5:load", "actual": {"a": 0}}], "layout-a", declaration
    )

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
