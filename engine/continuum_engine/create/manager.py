from __future__ import annotations

import json
import shutil
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


@dataclass
class Creator:
	id: str
	description: str
	dependencies: list[str]
	check: Callable[["CreateContext"], bool]
	create: Callable[["CreateContext"], None]
	verify: Callable[["CreateContext"], None]


@dataclass
class CreateContext:
	workspace: Path
	dry_run: bool
	debug: bool
	yes: bool


def _now_iso() -> str:
	return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _run(cmd: list[str], ctx: CreateContext, mutate: bool = False) -> subprocess.CompletedProcess:
	if mutate and ctx.dry_run:
		print(f"[dry-run] {' '.join(cmd)}")
		return subprocess.CompletedProcess(cmd, 0)
	if ctx.debug:
		return subprocess.run(cmd)
	return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _ensure_state_dir(ws: Path) -> Path:
	state_dir = ws / ".continuum" / "state"
	state_dir.mkdir(parents=True, exist_ok=True)
	return state_dir


def _load_state(ws: Path) -> dict:
	path = ws / ".continuum" / "state" / "create.json"
	if not path.exists():
		return {}
	try:
		return json.loads(path.read_text(encoding="utf-8"))
	except Exception:
		return {}


def _save_state(ws: Path, state: dict) -> None:
	state_dir = _ensure_state_dir(ws)
	path = state_dir / "create.json"
	path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _state_update(state: dict, cid: str, result: str, err: str | None) -> None:
	state[cid] = {
		"last_run": _now_iso(),
		"last_result": result,
		"last_error": err,
	}


def _cmd_exists(cmd: str) -> bool:
	return shutil.which(cmd) is not None


def _ollama_show(ctx: CreateContext, model: str) -> subprocess.CompletedProcess:
	if not _cmd_exists("ollama"):
		raise RuntimeError("ollama not installed. Run: continuum install ollama")
	return _run(["ollama", "show", model], ctx, mutate=False)


def _find_agent_modelfile(base: Path) -> Path | None:
	if not base.exists() or not base.is_dir():
		return None
	for p in base.rglob("*"):
		if not p.is_file():
			continue
		name = p.name
		if name == "Modelfile" or "modelfile" in name.lower():
			return p
	return None


def get_creators() -> dict[str, Creator]:
	def phi3_json_check(ctx: CreateContext) -> bool:
		res = _ollama_show(ctx, "phi3-mini-json:latest")
		return res.returncode == 0

	def phi3_json_create(ctx: CreateContext) -> None:
		path = ctx.workspace / "external" / "model_data_1o" / "models" / "phi3-mini-json" / "phi3-json-modelfile"
		if not path.exists():
			raise RuntimeError("Modelfile not found")
		res = _run(["ollama", "create", "phi3-mini-json:latest", "-f", str(path)], ctx, mutate=True)
		if res.returncode != 0 and not ctx.dry_run:
			err = res.stderr.strip() if isinstance(res.stderr, str) else ""
			msg = err or "ollama create failed"
			msg = f"{msg}\nIf a base model is missing, run: continuum pull data_models"
			raise RuntimeError(msg)

	def phi3_json_verify(ctx: CreateContext) -> None:
		res = _ollama_show(ctx, "phi3-mini-json:latest")
		if res.returncode != 0:
			raise RuntimeError("ollama show failed")

	def phi3_agent_check(ctx: CreateContext) -> bool:
		res = _ollama_show(ctx, "phi3-mini-agent:latest")
		return res.returncode == 0

	def phi3_agent_create(ctx: CreateContext) -> None:
		base = ctx.workspace / "external" / "model_data_1o" / "models" / "phi3-mini-agent"
		path = _find_agent_modelfile(base)
		if path is None or not path.is_file():
			raise RuntimeError(
				f"Modelfile not found under: {base}. Expected a file containing 'modelfile' or named 'Modelfile'."
			)
		res = _run(["ollama", "create", "phi3-mini-agent:latest", "-f", str(path)], ctx, mutate=True)
		if res.returncode != 0 and not ctx.dry_run:
			err = res.stderr.strip() if isinstance(res.stderr, str) else ""
			msg = err or "ollama create failed"
			msg = f"{msg}\nIf a base model is missing, run: continuum pull data_models"
			raise RuntimeError(msg)

	def phi3_agent_verify(ctx: CreateContext) -> None:
		res = _ollama_show(ctx, "phi3-mini-agent:latest")
		if res.returncode != 0:
			raise RuntimeError("ollama show failed")

	return {
		"phi3_mini_json": Creator(
			id="phi3_mini_json",
			description="Create phi3-mini-json model",
			dependencies=[],
			check=phi3_json_check,
			create=phi3_json_create,
			verify=phi3_json_verify,
		),
		"phi3_mini_agent": Creator(
			id="phi3_mini_agent",
			description="Create phi3-mini-agent model",
			dependencies=[],
			check=phi3_agent_check,
			create=phi3_agent_create,
			verify=phi3_agent_verify,
		),
	}


def get_bundles() -> dict[str, list[str]]:
	return {
		"engine": ["phi3_mini_json", "phi3_mini_agent"],
	}


def list_targets() -> None:
	creators = get_creators()
	bundles = get_bundles()
	print("Create targets:")
	for cid, c in creators.items():
		print(f"  {cid}: {c.description}")
	print("Bundles:")
	for bid, items in bundles.items():
		print(f"  {bid}: {', '.join(items)}")


def _resolve_targets(targets: list[str], creators: dict[str, Creator], bundles: dict[str, list[str]]) -> list[str]:
	resolved: list[str] = []
	visiting: set[str] = set()
	visited: set[str] = set()

	def visit(t: str) -> None:
		if t in visited:
			return
		if t in visiting:
			raise RuntimeError(f"Cycle detected in create targets: {t}")
		visiting.add(t)
		if t in bundles:
			for sub in bundles[t]:
				visit(sub)
		elif t in creators:
			for dep in creators[t].dependencies:
				visit(dep)
			resolved.append(t)
		else:
			raise RuntimeError(f"Unknown create target: {t}")
		visiting.remove(t)
		visited.add(t)

	for t in targets:
		visit(t)
	return resolved


def create_target(target: str, ctx: CreateContext) -> int:
	creators = get_creators()
	bundles = get_bundles()
	to_create = _resolve_targets([target], creators, bundles)
	print(f"Will create: {', '.join(to_create)}")
	if not ctx.yes and not ctx.dry_run:
		resp = input(f"Proceed with create of {', '.join(to_create)}? [y/N]: ").strip().lower()
		if resp not in {"y", "yes"}:
			print("Aborted.")
			return 1
	state = _load_state(ctx.workspace)
	for cid in to_create:
		c = creators[cid]
		try:
			if c.check(ctx):
				print(f"[ok] {cid} already created")
				_state_update(state, cid, "already_created", None)
			else:
				print(f"[run] creating {cid}...")
				c.create(ctx)
				c.verify(ctx)
				print(f"[ok] created {cid}")
				_state_update(state, cid, "success", None)
		except Exception as e:
			print(f"[err] {cid}: {e}")
			if ctx.debug:
				print(traceback.format_exc())
			_state_update(state, cid, "failed", str(e))
			if not ctx.dry_run:
				_save_state(ctx.workspace, state)
			return 1
	if not ctx.dry_run:
		_save_state(ctx.workspace, state)
	return 0


def run_doctor(ctx: CreateContext, json_output: bool = False) -> int:
	creators = get_creators()
	report = {"creators": {}}
	if not json_output:
		print("Create targets:")
	for cid, c in creators.items():
		status = "missing"
		reason = None
		try:
			if c.check(ctx):
				try:
					c.verify(ctx)
					status = "ready"
				except Exception as e:
					status = "broken"
					reason = str(e)
			else:
				status = "missing"
		except Exception as e:
			status = "broken"
			reason = str(e)
		report["creators"][cid] = {"status": status, "reason": reason}
		if not json_output:
			line = f"  {cid}: {status}"
			if reason and status == "broken":
				line += f" ({reason})"
			print(line)
	if json_output:
		print(json.dumps(report, indent=2))
	return 0
