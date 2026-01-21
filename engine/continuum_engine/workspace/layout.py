from __future__ import annotations

from pathlib import Path

DEFAULT_YAML = """# Continuum workspace config (V0)
workspace_name: "continuum-workspace"

stages:
	stage1_raw_dir: "datasets/stage1_raw"
	stage2_curated_dir: "datasets/stage2_curated"
	stage3_annotated_dir: "datasets/stage3_annotated"
"""

def init_workspace(ws: Path) -> None:
	ws.mkdir(parents=True, exist_ok=True)

	dirs = [
		ws / "data" / "raw",
		ws / "datasets",
		ws / "runs",
		ws / "models" / "checkpoints",
		ws / "models" / "exports",
		ws / "cache",
		ws / "logs",
	]

	for d in dirs:
		d.mkdir(parents=True, exist_ok=True)

	cfg = ws / "continuum.yaml"
	if not cfg.exists():
		cfg.write_text(DEFAULT_YAML, encoding="utf-8")
