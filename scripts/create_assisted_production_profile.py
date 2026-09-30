"""Fork a stopped assisted trial into a disclosed frozen production budget profile.
Never reset hardware, counts or original elapsed wall time.
"""

import hashlib
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from noob_agent.redstone.trial import TrialManifest

p = Path(sys.argv[1])
data = TrialManifest.load_data(p)
assert data["loop"]["status"] == "stopped"
assert all(
    e["kind"] in {"planner_call", "jev_call"} for e in data["events"] if e["outcome"] == "unknown"
)
assert data.get("timeline_resources", {}).get("state", "clean") == "clean"
stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
root = Path("demo/production-profiles") / stamp
root.mkdir(parents=True)
shutil.copy2(p, root / "original-manifest.json")
shutil.copy2(p.with_name("events.jsonl"), root / "original-events.jsonl")
contract_path = Path(data["contract"]["path"])
old_contract = json.loads(contract_path.read_text())
new_contract = json.loads(contract_path.read_text())
new_contract["version"] = "redstone-computer-v1"
new_contract["budgets"].update(
    wall_seconds=43200,
    primitive_actions=200000,
    planner_calls=2400,
    jev_calls=24000,
    repair_rounds=240,
)
new_path = root / "contract.json"
new_path.write_text(json.dumps(new_contract, indent=2) + "\n")
shutil.copy2(contract_path, root / "original-contract.json")
profile = {
    "kind": "disclosed assisted production, not an unaided benchmark",
    "frozen_at": datetime.now(UTC).isoformat(),
    "parent_manifest": str(p),
    "parent_sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
    "original_limits": old_contract["budgets"],
    "production_limits": new_contract["budgets"],
    "counts_and_elapsed": "all retained from original trial; no resets",
    "hardware": "retained; no world mutation",
    "reason": "complete authorized module-gated computer and Replay-only demonstration",
}
(root / "profile.json").write_text(json.dumps(profile, indent=2) + "\n")
new_id = stamp + "-assisted-production"
dest = p.parent.parent / new_id
dest.mkdir()
shutil.copy2(p.with_name("events.jsonl"), dest / "events.jsonl")
data["run_id"] = new_id
data["production_profile"] = profile
data["contract"] = {
    "version": new_contract["version"],
    "path": str(new_path),
    "sha256": hashlib.sha256(new_path.read_bytes()).hexdigest(),
}
data["limits"] = new_contract["budgets"]
data["action_budget"]["used"] = sum(
    e["kind"]
    in {
        "charged_observation",
        "bounded_action",
        "bounded_movement",
        "bounded_command",
        "grader_operation",
    }
    for e in data["events"]
)
data["action_budget"]["limit"] = 200000
(dest / "manifest.json").write_text(json.dumps(data, indent=2) + "\n")
print(dest / "manifest.json")
