"""Publish the allowlisted queue replay aggregate to the static site."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "app/frontend/public/data"
SOURCE = ROOT / "result/queue_replay/public_summary.json"


def export(output: Path = PUBLIC) -> None:
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    events = payload.get("events", [])
    sensitivity = payload.get("sensitivity", [])
    expected = {row["event_date"] for row in events}
    if payload.get("schema_version") != 1 or len(events) != 9 or len(expected) != 9:
        raise ValueError("Unexpected queue replay event cohort")
    if len(sensitivity) != 81 or {row["event_date"] for row in sensitivity} != expected:
        raise ValueError("Incomplete queue replay sensitivity grid")
    if not payload.get("assumptions", {}).get("actual_is_hindsight"):
        raise ValueError("Observed counts must be labelled retrospective")
    output.mkdir(parents=True, exist_ok=True)
    target = output / "queue-replay.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    manifest_path = output / "manifest.json"
    if manifest_path.exists():
        newline = "\r\n" if b"\r\n" in manifest_path.read_bytes() else "\n"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["outputs"][target.name] = hashlib.sha256(target.read_bytes()).hexdigest()
        manifest_path.write_bytes((json.dumps(manifest, ensure_ascii=False, indent=2).replace("\n", newline) + newline).encode("utf-8"))


if __name__ == "__main__":
    export()
