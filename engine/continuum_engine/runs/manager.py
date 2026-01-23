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
	stdout_path: Path
	stderr_path: Path

def create_run(workspace: Path, command: str) -> Run:
	runs_root = workspace / ".continuum" / "runs"
	runs_root.mkdir(parents=True, exist_ok=True)

	today = datetime.utcnow().strftime("%Y-%m-%d")
	existing = sorted([p for p in runs_root.glob(f"run_{today}_*") if p.is_dir()])
	n = len(existing) + 1
	run_id = f"run_{today}_{n:03d}"

	run_dir = runs_root / run_id
	run_dir.mkdir(parents=True, exist_ok=False)

	meta_path = run_dir / "run.json"
	stdout_path = run_dir / "stdout.log"
	stderr_path = run_dir / "stderr.log"

	meta = {
		"run_id": run_id,
		"command": command,
		"workspace": str(workspace),
		"status": "running",
		"started_at": _now_iso(),
		"finished_at": None,
		"stdout_path": str(stdout_path),
		"stderr_path": str(stderr_path),
	}
	meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
	stdout_path.write_text("", encoding="utf-8")
	stderr_path.write_text("", encoding="utf-8")
	return Run(
		run_id=run_id,
		run_dir=run_dir,
		meta_path=meta_path,
		stdout_path=stdout_path,
		stderr_path=stderr_path,
	)

def finish_run(run: Run, status: str) -> None:
	meta = json.loads(run.meta_path.read_text(encoding="utf-8"))
	meta["status"] = status
	meta["finished_at"] = _now_iso()
	run.meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

def list_runs(workspace: Path) -> list[Path]:
	runs_root = workspace / ".continuum" / "runs"
	if not runs_root.exists():
		raise FileNotFoundError(f"Runs folder not found: {runs_root}")
	if not runs_root.is_dir():
		raise NotADirectoryError(f"Runs path is not a directory: {runs_root}")
	return sorted([p for p in runs_root.iterdir() if p.is_dir()], key=lambda p: p.name, reverse=True)

def read_run_meta(run_dir: Path) -> dict:
	meta_path = run_dir / "run.json"
	if not meta_path.exists():
		raise FileNotFoundError(f"Missing run.json: {meta_path}")
	return json.loads(meta_path.read_text(encoding="utf-8"))
