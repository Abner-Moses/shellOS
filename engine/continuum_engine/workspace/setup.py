from __future__ import annotations

import os
import subprocess
from pathlib import Path


def ensure_venv_active() -> bool:
	return bool(os.environ.get("VIRTUAL_ENV"))


def repo_root_from_here() -> Path:
	return Path(__file__).resolve().parents[3]


def run_cmd(cmd: list[str]) -> None:
	result = subprocess.run(cmd, check=False)
	if result.returncode != 0:
		raise RuntimeError(f"Command failed: {' '.join(cmd)} (exit {result.returncode})")


def generate_requirements(path: Path, profile: str) -> None:
	minimal = [
		"pyyaml",
		"rich",
		"tqdm",
		"psutil",
		"jsonlines",
	]
	if profile == "minimal":
		pkgs = minimal
	else:
		pkgs = minimal + [
			"numpy",
			"torch",
			"transformers",
			"datasets",
			"accelerate",
			"safetensors",
		]
	path.write_text("\n".join(pkgs) + "\n", encoding="utf-8")
