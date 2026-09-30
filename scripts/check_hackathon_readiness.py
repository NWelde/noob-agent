"""Inspect retained evidence and final video metadata without touching Minecraft."""

import argparse
import json
import shutil
import subprocess
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any

from noob_agent.redstone.readiness import trial_readiness
from noob_agent.redstone.trial import TrialManifest


def video_readiness(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {"format_ready": False, "reason": "Final video is missing"}
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return {"format_ready": False, "reason": "ffprobe is unavailable"}
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path.resolve()),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        metadata = json.loads(result.stdout)
        videos = [s for s in metadata["streams"] if s.get("codec_type") == "video"]
        audio = [s for s in metadata["streams"] if s.get("codec_type") == "audio"]
        duration = float(metadata["format"]["duration"])
        video = videos[0] if videos else {}
        checks = {
            "duration_90_to_120_seconds": 90 <= duration <= 120,
            "resolution_1920x1080": (video.get("width"), video.get("height")) == (1920, 1080),
            "h264_video": video.get("codec_name") == "h264",
            "60_frames_per_second": Fraction(video.get("avg_frame_rate", "0")) == 60,
            "aac_audio": any(s.get("codec_name") == "aac" for s in audio),
            "mp4_container": "mp4" in metadata["format"].get("format_name", "").split(","),
        }
        return {
            "format_ready": all(checks.values()),
            "duration_seconds": duration,
            "checks": checks,
            "scope": "metadata_only_content_and_replay_provenance_require_review",
        }
    except (
        OSError,
        subprocess.SubprocessError,
        ValueError,
        KeyError,
        TypeError,
        ZeroDivisionError,
    ):
        return {"format_ready": False, "reason": "Video metadata could not be verified"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--video", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        hardware = trial_readiness(TrialManifest.load_data(args.manifest))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        hardware = {"hardware_ready": False, "reason": "Trial evidence could not be verified"}
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "hardware": hardware,
        "video": video_readiness(args.video),
        "event": {
            "date": "2026-10-01",
            "timezone": "America/Los_Angeles",
            "presentations_start": "15:20",
            "mandatory_check_in_deadline": "16:15",
            "awards": "16:30–16:50",
            "zoom": "https://coreweave.zoom.us/j/85491824907",
            "in_person": "Expo Stage, Moscone, 747 Howard St; back left of Expo Hall",
            "check_in_with": "Anna (CoreWeave) or Alexa (AGI House)",
        },
        "manual_checks": [
            "Rehearse the final live machine and backup playback",
            "Review normal-speed proof footage, legibility, and Replay provenance",
            "Check in by 4:15pm PT and remain present for awards",
        ],
    }
    report["evidence_gates_passed"] = hardware["hardware_ready"] and report["video"]["format_ready"]
    encoded = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["evidence_gates_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
