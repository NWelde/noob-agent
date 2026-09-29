import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from compare_redstone_trials import summarize_manifests


def test_summarizes_per_model_failure_metrics_without_prompt_content():
    manifests = [
        {
            "model": {"identity": "deepseek-test"},
            "events": [
                {"kind": "planner_timeout"},
                {"kind": "planner_validation", "result": {"accepted": False}},
                {"kind": "bounded_action", "result": {"skipped": "identical_verified_placement"}},
            ],
            "checks": [
                {
                    "module": "register",
                    "passed": False,
                    "checks": [{"name": "probe A", "passed": False}],
                }
            ],
            "final_grade": {"model_success": False},
            "public_prompts": [{"system": "SECRET PROMPT"}],
        },
        {
            "model": {"identity": "deepseek-test"},
            "events": [],
            "checks": [],
            "final_grade": {"model_success": True},
        },
        {
            "model": {"identity": "astra-test"},
            "events": [],
            "checks": [{"module": "register", "passed": True, "checks": []}],
            "final_grade": {"model_success": True},
        },
    ]

    report = summarize_manifests(manifests)

    deepseek = report["models"]["deepseek-test"]
    assert deepseek == {
        "trials": 2,
        "provider_timeouts": 1,
        "planner_validation_rejections": 1,
        "duplicate_placements": 1,
        "module_grades": 1,
        "module_grade_failures": 1,
        "probe_failures": 1,
        "final_successes": 1,
        "rates": {
            "provider_timeout": 0.5,
            "planner_validation_rejection": 0.5,
            "duplicate_placement": 0.5,
            "module_grade_failure": 1.0,
            "probe_failure": 1.0,
            "final_success": 0.5,
        },
    }
    assert report["models"]["astra-test"]["final_successes"] == 1
    assert "SECRET PROMPT" not in json.dumps(report)


def test_identifies_models_from_either_manifest_model_field():
    report = summarize_manifests([{"planner_model": "astra-id", "events": []}])
    assert list(report["models"]) == ["astra-id"]
