from __future__ import annotations

import json
import os
import shutil
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


@dataclass
class Installer:
	id: str
	description: str
	dependencies: list[str]
	check: Callable[[], bool]
	install: Callable[["InstallContext"], None]
	verify: Callable[["InstallContext"], None]


@dataclass
class InstallContext:
	workspace: Path
	dry_run: bool
	debug: bool
	yes: bool
	apt_updated: bool = False


def _now_iso() -> str:
	return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _is_root() -> bool:
	try:
		return os.geteuid() == 0
	except Exception:
		return False


def _ensure_sudo() -> list[str]:
	if _is_root():
		return []
	if shutil.which("sudo"):
		print("sudo required")
		return ["sudo"]
	raise RuntimeError("sudo not found; run as root or install sudo.")


def _run(cmd: list[str], ctx: InstallContext, env: dict | None = None) -> None:
	if ctx.dry_run:
		print(f"[dry-run] {' '.join(cmd)}")
		return
	if ctx.debug:
		result = subprocess.run(cmd, env=env)
	else:
		result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
	if result.returncode != 0:
		err = result.stderr.strip() if isinstance(result.stderr, str) else ""
		msg = f"Command failed: {' '.join(cmd)}"
		if "Could not get lock" in err or "Unable to acquire the dpkg frontend lock" in err:
			msg = f"{msg}\nAPT is locked; another apt/dpkg process is running; wait and retry."
		if "permission denied" in err.lower():
			msg = f"{msg}\nPermission denied; try running with sudo."
		if ctx.debug and err:
			msg = f"{msg}\n{err}"
		raise RuntimeError(msg)


def _apt_update(ctx: InstallContext) -> None:
	if ctx.apt_updated:
		return
	prefix = _ensure_sudo()
	env = os.environ.copy()
	env["DEBIAN_FRONTEND"] = "noninteractive"
	cmd = prefix + ["apt-get", "update"]
	if ctx.dry_run:
		print(f"[dry-run] {' '.join(cmd)}")
		ctx.apt_updated = True
		return
	if ctx.debug:
		result = subprocess.run(cmd, env=env)
	else:
		result = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
	if result.returncode != 0:
		err = result.stderr.strip() if isinstance(result.stderr, str) else ""
		msg = "apt-get update failed"
		if "Could not get lock" in err or "Unable to acquire the dpkg frontend lock" in err:
			msg = f"{msg}\nAPT is locked; another apt/dpkg process is running; wait and retry."
		if "permission denied" in err.lower():
			msg = f"{msg}\nPermission denied; try running with sudo."
		if ctx.debug and err:
			msg = f"{msg}\n{err}"
		raise RuntimeError(msg)
	ctx.apt_updated = True


def _apt_install(pkgs: list[str], ctx: InstallContext) -> None:
	_apt_update(ctx)
	prefix = _ensure_sudo()
	env = os.environ.copy()
	env["DEBIAN_FRONTEND"] = "noninteractive"
	cmd = prefix + ["apt-get", "install", "-y"] + pkgs
	if ctx.dry_run:
		print(f"[dry-run] {' '.join(cmd)}")
		return
	if ctx.debug:
		result = subprocess.run(cmd, env=env)
	else:
		result = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
	if result.returncode != 0:
		err = result.stderr.strip() if isinstance(result.stderr, str) else ""
		msg = f"apt-get install failed: {' '.join(pkgs)}"
		if "Could not get lock" in err or "Unable to acquire the dpkg frontend lock" in err:
			msg = f"{msg}\nAPT is locked; another apt/dpkg process is running; wait and retry."
		if "permission denied" in err.lower():
			msg = f"{msg}\nPermission denied; try running with sudo."
		if ctx.debug and err:
			msg = f"{msg}\n{err}"
		raise RuntimeError(msg)


def _dpkg_installed(pkg: str) -> bool:
	result = subprocess.run(["dpkg", "-s", pkg], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
	return result.returncode == 0


def _cmd_exists(cmd: str) -> bool:
	return shutil.which(cmd) is not None


def _ensure_state_dir(ws: Path) -> Path:
	state_dir = ws / ".continuum" / "state"
	state_dir.mkdir(parents=True, exist_ok=True)
	return state_dir


def _load_state(ws: Path) -> dict:
	path = ws / ".continuum" / "state" / "install.json"
	if not path.exists():
		return {}
	try:
		return json.loads(path.read_text(encoding="utf-8"))
	except Exception:
		return {}


def _save_state(ws: Path, state: dict) -> None:
	state_dir = _ensure_state_dir(ws)
	path = state_dir / "install.json"
	path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _installer_state_update(state: dict, iid: str, result: str, err: str | None) -> None:
	state[iid] = {
		"last_run": _now_iso(),
		"last_result": result,
		"last_error": err,
	}


def _verify_cmd(cmd: list[str], ctx: InstallContext) -> None:
	if ctx.debug:
		result = subprocess.run(cmd)
	else:
		result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
	if result.returncode != 0:
		raise RuntimeError(f"Command failed: {' '.join(cmd)}")


def get_installers() -> dict[str, Installer]:
	installers: dict[str, Installer] = {}

	def apt_installer(pkg: str, desc: str, verify_cmd: list[str] | None = None) -> Installer:
		return Installer(
			id=pkg,
			description=desc,
			dependencies=[],
			check=lambda p=pkg: _dpkg_installed(p),
			install=lambda ctx, p=pkg: _apt_install([p], ctx),
			verify=(lambda ctx, cmd=verify_cmd: _verify_cmd(cmd, ctx)) if verify_cmd else (lambda ctx: None),
		)

	installers["curl"] = apt_installer("curl", "Command-line HTTP client", ["curl", "--version"])
	installers["git"] = apt_installer("git", "Git version control", ["git", "--version"])
	installers["ca-certificates"] = apt_installer("ca-certificates", "CA certificates")
	installers["unzip"] = apt_installer("unzip", "Zip extraction utility", ["unzip", "-v"])
	installers["build-essential"] = apt_installer("build-essential", "Build tools")
	installers["python3"] = apt_installer("python3", "Python 3", ["python3", "--version"])
	installers["python3-venv"] = apt_installer("python3-venv", "Python venv support")
	installers["python3-pip"] = apt_installer("python3-pip", "Python package installer", ["pip3", "--version"])

	# Node install: use Ubuntu's nodejs package for stability on servers.
	def node_check() -> bool:
		return _dpkg_installed("nodejs")

	def node_install(ctx: InstallContext) -> None:
		_apt_install(["nodejs"], ctx)

	def node_verify(ctx: InstallContext) -> None:
		_verify_cmd(["node", "--version"], ctx)

	installers["node"] = Installer(
		id="node",
		description="Node.js runtime",
		dependencies=[],
		check=node_check,
		install=node_install,
		verify=node_verify,
	)

	def ollama_check() -> bool:
		return _cmd_exists("ollama")

	def ollama_install(ctx: InstallContext) -> None:
		prefix = _ensure_sudo()
		cmd = prefix + ["sh", "-c", "curl -fsSL https://ollama.com/install.sh | sh"]
		if ctx.dry_run:
			print(f"[dry-run] {' '.join(cmd)}")
		else:
			_run(cmd, ctx)
			_run(prefix + ["systemctl", "enable", "--now", "ollama"], ctx)

	def ollama_verify(ctx: InstallContext) -> None:
		_verify_cmd(["ollama", "--version"], ctx)

	installers["ollama"] = Installer(
		id="ollama",
		description="Ollama local model runner",
		dependencies=["curl", "ca-certificates"],
		check=ollama_check,
		install=ollama_install,
		verify=ollama_verify,
	)

	return installers


def get_bundles() -> dict[str, list[str]]:
	return {
		"base": ["curl", "git", "ca-certificates", "unzip", "build-essential", "python3", "python3-venv", "python3-pip"],
		"web": ["node"],
		"ai": ["ollama"],
		"full": ["base", "web", "ai"],
	}


def list_targets() -> None:
	installers = get_installers()
	bundles = get_bundles()
	print("Installers:")
	for iid, inst in installers.items():
		print(f"  {iid}: {inst.description}")
	print("Bundles:")
	for bid, items in bundles.items():
		print(f"  {bid}: {', '.join(items)}")


def _resolve_targets(targets: list[str], installers: dict[str, Installer], bundles: dict[str, list[str]]) -> list[str]:
	resolved: list[str] = []
	visiting: set[str] = set()
	visited: set[str] = set()

	def visit(t: str) -> None:
		if t in visited:
			return
		if t in visiting:
			raise RuntimeError(f"Cycle detected in install targets: {t}")
		visiting.add(t)
		if t in bundles:
			for sub in bundles[t]:
				visit(sub)
		elif t in installers:
			for dep in installers[t].dependencies:
				visit(dep)
			resolved.append(t)
		else:
			raise RuntimeError(f"Unknown install target: {t}")
		visiting.remove(t)
		visited.add(t)

	for t in targets:
		visit(t)
	return resolved


def install_target(target: str, ctx: InstallContext) -> int:
	installers = get_installers()
	bundles = get_bundles()
	to_install = _resolve_targets([target], installers, bundles)
	print(f"Will install: {', '.join(to_install)}")
	if not ctx.yes and not ctx.dry_run:
		resp = input(f"Proceed with install of {', '.join(to_install)}? [y/N]: ").strip().lower()
		if resp not in {"y", "yes"}:
			print("Aborted.")
			return 1
	state = _load_state(ctx.workspace)
	for iid in to_install:
		inst = installers[iid]
		try:
			if inst.check():
				print(f"[ok] {iid} already installed")
				_installer_state_update(state, iid, "already_installed", None)
			else:
				print(f"[run] installing {iid}...")
				inst.install(ctx)
				inst.verify(ctx)
				print(f"[ok] installed {iid}")
				_installer_state_update(state, iid, "success", None)
		except Exception as e:
			print(f"[err] {iid}: {e}")
			if ctx.debug:
				print(traceback.format_exc())
			_installer_state_update(state, iid, "failed", str(e))
			if not ctx.debug:
				pass
			if not ctx.dry_run:
				_save_state(ctx.workspace, state)
			return 1
	if not ctx.dry_run:
		_save_state(ctx.workspace, state)
	return 0


def run_doctor(ctx: InstallContext, json_output: bool = False) -> int:
	installers = get_installers()
	defaults = get_bundles()
	default_targets = []
	for k in ["base", "web", "ai"]:
		default_targets.extend(defaults.get(k, []))
	seen = set()
	default_targets = [t for t in default_targets if not (t in seen or seen.add(t))]
	report = {"installers": {}, "ollama": {}}
	if not json_output:
		print("Installers:")
	for iid in default_targets:
		inst = installers.get(iid)
		if not inst:
			continue
		if not inst.check():
			if not json_output:
				print(f"  {iid}: missing")
			report["installers"][iid] = {"status": "missing", "reason": None}
			continue
		try:
			inst.verify(ctx)
			if not json_output:
				print(f"  {iid}: installed (ok)")
			report["installers"][iid] = {"status": "installed (ok)", "reason": None}
		except Exception:
			if not json_output:
				print(f"  {iid}: installed (broken)")
			report["installers"][iid] = {"status": "installed (broken)", "reason": None}

	if ctx.debug and not json_output:
		cmd_checks = {
			"curl": ["curl", "--version"],
			"git": ["git", "--version"],
			"python3": ["python3", "--version"],
			"node": ["node", "--version"],
			"ollama": ["ollama", "--version"],
		}
		for name, cmd in cmd_checks.items():
			if not _cmd_exists(cmd[0]):
				print(f"  {name}: missing command")
				continue
			result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
			if result.returncode == 0:
				print(f"  {name}: ok")
			else:
				print(f"  {name}: error")
	if _cmd_exists("ollama"):
		ollama_reason = None
		result = subprocess.run(["ollama", "list"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
		if result.returncode != 0:
			ollama_reason = "ollama list failed"
		if shutil.which("systemctl"):
			active = subprocess.run(["systemctl", "is-active", "ollama"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
			if active.returncode != 0:
				ollama_reason = "ollama service inactive"
		if ollama_reason:
			if not json_output:
				print("  ollama: installed (broken)")
				print(f"    reason: {ollama_reason}")
			report["ollama"] = {"status": "installed (broken)", "reason": ollama_reason}
		else:
			if not json_output:
				print("  ollama: installed (ok)")
			report["ollama"] = {"status": "installed (ok)", "reason": None}
	if json_output:
		print(json.dumps(report, indent=2))
	return 0
