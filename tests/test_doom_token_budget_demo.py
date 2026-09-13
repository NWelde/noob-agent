"""The token-budget Doom demo is isolated from normal evaluation runs."""

# ruff: noqa: E501

from __future__ import annotations

import importlib.util

import pytest


def _module():
    spec = importlib.util.spec_from_file_location(
        "doom_budget", "scripts/run_doom_learning_sequence.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_budget_mode_has_its_approved_defaults() -> None:
    options = _module()._parse_args(["--live-demo", "--token-budget", "1000000"])
    assert options.token_budget == 1_000_000
    assert options.builder_max_output_tokens == 100_000
    assert options.max_repairs == 3
    assert options.deadline_seconds == 3_600


@pytest.mark.parametrize("args", [["--token-budget", "1"], ["--live-demo", "--token-budget", "0"]])
def test_budget_mode_is_live_demo_only_and_bounded(args: list[str]) -> None:
    with pytest.raises(SystemExit):
        _module()._parse_args(args)


@pytest.mark.parametrize(
    "args",
    [
        ["--live-demo", "--token-budget", "1000001"],
        ["--live-demo", "--builder-max-output-tokens", "100001"],
    ],
)
def test_budget_mode_refuses_values_above_the_new_caps(args: list[str]) -> None:
    with pytest.raises(SystemExit):
        _module()._parse_args(args)
