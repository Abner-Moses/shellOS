from __future__ import annotations

from pathlib import Path


def ensure_workspace(ws: Path, require_init: bool = True) -> None:
	if not ws.exists():
		raise FileNotFoundError(f"Workspace path does not exist: {ws}")
	if not ws.is_dir():
		raise NotADirectoryError(f"Workspace path is not a directory: {ws}")
	if require_init and not (ws / ".continuum").exists():
		raise FileNotFoundError("Not a Continuum workspace. Run `continuum init`.")
