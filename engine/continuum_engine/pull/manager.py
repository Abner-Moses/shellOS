from __future__ import annotations

import json
import shutil
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

DATA_MODELS = [
	"goekdenizguelmez/JOSIEFIED-Qwen3",
	"phi3:mini",
]


@dataclass
class Puller:
	id: str
	description: str
	dependencies: list[str]
	check: Callable[["PullContext"], bool]
	pull: Callable[["PullContext"], None]
	verify: Callable[["PullContext"], None]


@dataclass
class PullContext:
	workspace: Path
	dry_run: bool
	debug: bool
	yes: bool


def _now_iso() -> str:
	return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _run(cmd: list[str], ctx: PullContext, mutate: bool = False) -> subprocess.CompletedProcess:
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
	path = ws / ".continuum" / "state" / "pull.json"
	if not path.exists():
		return {}
	try:
		return json.loads(path.read_text(encoding="utf-8"))
	except Exception:
		return {}


def _save_state(ws: Path, state: dict) -> None:
	state_dir = _ensure_state_dir(ws)
	path = state_dir / "pull.json"
	path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _state_update(state: dict, pid: str, result: str, err: str | None) -> None:
	state[pid] = {
		"last_run": _now_iso(),
		"last_result": result,
		"last_error": err,
	}


def _cmd_exists(cmd: str) -> bool:
	return shutil.which(cmd) is not None


def _ollama_list(ctx: PullContext) -> tuple[bool, set[str], str | None]:
	if not _cmd_exists("ollama"):
		return False, set(), "ollama not installed. Run: continuum install ollama"
	result = _run(["ollama", "list"], ctx, mutate=False)
	if result.returncode != 0:
		err = ""
		if isinstance(result.stderr, str):
			err = result.stderr.strip()
		return False, set(), err or "ollama list failed"
	output = ""
	if isinstance(result.stdout, str):
		output = result.stdout
	models: set[str] = set()
	for line in output.splitlines():
		if not line.strip() or line.lower().startswith("name"):
			continue
		parts = line.split()
		if parts:
			models.add(parts[0])
	return True, models, None


def get_pullers() -> dict[str, Puller]:
	def data_models_check(ctx: PullContext) -> bool:
		ok, models, _ = _ollama_list(ctx)
		if not ok:
			return False
		return all(m in models for m in DATA_MODELS)

	def data_models_pull(ctx: PullContext) -> None:
		ok, models, err = _ollama_list(ctx)
		if not ok:
			raise RuntimeError(err or "ollama list failed")
		missing = [m for m in DATA_MODELS if m not in models]
		for m in missing:
			res = _run(["ollama", "pull", m], ctx, mutate=True)
			if res.returncode != 0 and not ctx.dry_run:
				raise RuntimeError(f"ollama pull failed: {m}")

	def data_models_verify(ctx: PullContext) -> None:
		ok, models, err = _ollama_list(ctx)
		if not ok:
			raise RuntimeError(err or "ollama list failed")
		missing = [m for m in DATA_MODELS if m not in models]
		if missing:
			raise RuntimeError(f"missing models: {', '.join(missing)}")

	return {
		"data_models": Puller(
			id="data_models",
			description="Ollama models for Model_Data-1O",
			dependencies=[],
			check=data_models_check,
			pull=data_models_pull,
			verify=data_models_verify,
		),
	}


def list_targets() -> None:
	pullers = get_pullers()
	print("Pull targets:")
	for pid, p in pullers.items():
		print(f"  {pid}: {p.description}")


def _resolve_targets(targets: list[str], pullers: dict[str, Puller]) -> list[str]:
	resolved: list[str] = []
	visiting: set[str] = set()
	visited: set[str] = set()

	def visit(t: str) -> None:
		if t in visited:
			return
		if t in visiting:
			raise RuntimeError(f"Cycle detected in pull targets: {t}")
		visiting.add(t)
		if t in pullers:
			for dep in pullers[t].dependencies:
				visit(dep)
			resolved.append(t)
		else:
			raise RuntimeError(f"Unknown pull target: {t}")
		visiting.remove(t)
		visited.add(t)

	for t in targets:
		visit(t)
	return resolved


def pull_target(target: str, ctx: PullContext) -> int:
	pullers = get_pullers()
	to_pull = _resolve_targets([target], pullers)
	print(f"Will pull: {', '.join(to_pull)}")
	if not ctx.yes and not ctx.dry_run:
		resp = input(f"Proceed with pull of {', '.join(to_pull)}? [y/N]: ").strip().lower()
		if resp not in {"y", "yes"}:
			print("Aborted.")
			return 1
	state = _load_state(ctx.workspace)
	for pid in to_pull:
		p = pullers[pid]
		try:
			if p.check(ctx):
				print(f"[ok] {pid} already present")
				_state_update(state, pid, "already_present", None)
			else:
				print(f"[run] pulling {pid}...")
				p.pull(ctx)
				p.verify(ctx)
				print(f"[ok] pulled {pid}")
				_state_update(state, pid, "success", None)
		except Exception as e:
			print(f"[err] {pid}: {e}")
			if ctx.debug:
				print(traceback.format_exc())
			_state_update(state, pid, "failed", str(e))
			if not ctx.dry_run:
				_save_state(ctx.workspace, state)
			return 1
	if not ctx.dry_run:
		_save_state(ctx.workspace, state)
	return 0


def run_doctor(ctx: PullContext, json_output: bool = False) -> int:
	pullers = get_pullers()
	report = {"pullers": {}}
	if not json_output:
		print("Pull targets:")
	for pid, p in pullers.items():
		status = "missing"
		details = {}
		try:
			if p.check(ctx):
				try:
					p.verify(ctx)
					status = "ready"
				except Exception as e:
					status = "broken"
					details["error"] = str(e)
			else:
				status = "missing"
		except Exception as e:
			status = "broken"
			details["error"] = str(e)
		if pid == "data_models":
			ok, models, err = _ollama_list(ctx)
			if ok:
				missing = [m for m in DATA_MODELS if m not in models]
				if missing:
					details["missing_models"] = missing
			else:
				details["error"] = err or "ollama list failed"
		report["pullers"][pid] = {"status": status, **details}
		if not json_output:
			line = f"  {pid}: {status}"
			if "missing_models" in details:
				line += f" (missing: {', '.join(details['missing_models'])})"
			print(line)
	if json_output:
		print(json.dumps(report, indent=2))
	return 0
