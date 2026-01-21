from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

def _now_iso() -> str:
	return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

@dataclass(frozen=True)
class Run:
	run_id: str
	run_dir: Path
	meta_path: Path
	log_path: Path

def create_run(workspace: Path, command: str) -> Run:
	runs_root = workspace / "runs"
	runs_root.mkdir(parents=True, exist_ok=True)

	today = datetime.utcnow().strftime("%Y-%m-%d")
	existing = sorted([p for p in runs_root.glob(f"run_{today}_*") if p.is_dir()])
	n = len(existing) + 1
	run_id = f"run_{today}_{n:03d}"

	run_dir = runs_root / run_id
	run_dir.mkdir(parents=True, exist_ok=False)

	meta_path = run_dir / "run.json"
	log_path = run_dir / "logs.txt"

	meta = {
		"run_id": run_id,
		"command": command,
		"workpsace": str(workspace),
		"status": "running",
		"started_at": _now_iso(),
		"finished_at": None,
	}
	meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
	log_path.write_text("", encoding="utf-8")
	return Run(run_id=run_id, run_dir=run_dir, meta_path=meta_path, log_path=log_path)

def finish_run(run: Run, status: str) -> None:
	meta = json.loads(run.meta_path.read_text(encoding="utf-8"))
	meta["status"] = status
	meta["finished_at"] = _now_iso()
	run.meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
