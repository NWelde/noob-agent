"""Compare existing redstone trial manifests by planner model (offline, no provider calls)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _model_name(manifest: dict[str, Any]) -> str:
    model = manifest.get("model")
    if isinstance(model, dict):
        identity = model.get("identity") or model.get("name") or model.get("model")
        if identity:
            return str(identity)
    return str(manifest.get("planner_model") or "unknown")


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _module_grades(manifest: dict[str, Any]) -> tuple[int, int, int]:
    grades = failures = probe_failures = 0
    for record in manifest.get("checks", []):
        if not isinstance(record, dict) or not record.get("module"):
            continue
        grades += 1
        if record.get("passed") is False:
            failures += 1
        for item in _walk(record.get("checks", [])):
            if item.get("passed") is False and any(
                token in str(item.get(key, "")).lower()
                for key in ("name", "probe", "signal", "reason")
                for token in ("probe", "signal")
            ):
                probe_failures += 1
    return grades, failures, probe_failures


def _rate(count: int, trials: int) -> float:
    return round(count / trials, 4) if trials else 0.0


def summarize_manifests(manifests: list[dict[str, Any]]) -> dict[str, Any]:
    """Return aggregate counts and per-trial rates; never copies prompt/response text."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    unattributed = 0
    for manifest in manifests:
        model = _model_name(manifest)
        if model == "unknown":
            unattributed += 1
            continue
        grouped.setdefault(model, []).append(manifest)

    models: dict[str, Any] = {}
    for model, trials in sorted(grouped.items()):
        timeouts = rejections = duplicates = grades = grade_failures = probes = successes = 0
        for manifest in trials:
            events = manifest.get("events", [])
            timeouts += sum(
                isinstance(e, dict) and e.get("kind") == "planner_timeout" for e in events
            )
            rejections += sum(
                isinstance(e, dict)
                and e.get("kind") == "planner_validation"
                and isinstance(e.get("result"), dict)
                and e["result"].get("accepted") is False
                for e in events
            )
            duplicates += sum(
                isinstance(e, dict)
                and e.get("kind") == "bounded_action"
                and isinstance(e.get("result"), dict)
                and e["result"].get("skipped") == "identical_verified_placement"
                for e in events
            )
            g, gf, pf = _module_grades(manifest)
            grades += g
            grade_failures += gf
            probes += pf
            successes += manifest.get("final_grade", {}).get("model_success") is True
        n = len(trials)
        models[model] = {
            "trials": n,
            "provider_timeouts": timeouts,
            "planner_validation_rejections": rejections,
            "duplicate_placements": duplicates,
            "module_grades": grades,
            "module_grade_failures": grade_failures,
            "probe_failures": probes,
            "final_successes": successes,
            "rates": {
                "provider_timeout": _rate(timeouts, n),
                "planner_validation_rejection": _rate(rejections, n),
                "duplicate_placement": _rate(duplicates, n),
                "module_grade_failure": _rate(grade_failures, grades),
                "probe_failure": _rate(probes, grades),
                "final_success": _rate(successes, n),
            },
        }
    return {"models": models, "unattributed_trials": unattributed}


def _manifest_paths(inputs: list[Path]) -> list[Path]:
    paths: set[Path] = set()
    for path in inputs:
        if path.is_dir():
            paths.update(path.rglob("manifest.json"))
        else:
            paths.add(path)
    return sorted(paths)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "manifests", nargs="+", type=Path, help="manifest JSON files or directories"
    )
    args = parser.parse_args(argv)
    data = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in _manifest_paths(args.manifests)
    ]
    print(json.dumps(summarize_manifests(data), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
