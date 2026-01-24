from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
import time
import shlex
import subprocess
import traceback
from pathlib import Path

from continuum_engine.workspace.layout import init_workspace
from continuum_engine.workspace.validate import ensure_workspace
from continuum_engine.runs.manager import create_run, finish_run, list_runs, read_run_meta
from continuum_engine.workspace.setup import (
	ensure_venv_active,
	generate_requirements,
	repo_root_from_here,
	run_cmd,
)
from continuum_engine.install import (
	InstallContext,
	install_target,
	list_targets,
	run_doctor,
)
from continuum_engine.pull import (
	PullContext,
	list_targets as list_pull_targets,
	pull_target,
	run_doctor as run_pull_doctor,
)
from continuum_engine.create import (
	CreateContext,
	list_targets as list_create_targets,
	create_target,
	run_doctor as run_create_doctor,
)


def build_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(prog="continuum")
	sub = parser.add_subparsers(dest="cmd", required=True)
	
	p_init = sub.add_parser("init", help="Initialize a Continuum workspace")
	p_init.add_argument("--workspace", help="Path to workspace folder (default: current directory)")

	p_runs = sub.add_parser("runs", help="Run history commands")
	p_runs_sub = p_runs.add_subparsers(dest="runs_cmd", required=True)

	p_runs_list = p_runs_sub.add_parser("list", help="List runs")
	p_runs_list.add_argument("--workspace", help="Path to workspace folder (default: current directory)")
	p_runs_list.add_argument("--json", action="store_true", help="Output JSON")

	p_runs_show = p_runs_sub.add_parser("show", help="Show a run")
	p_runs_show.add_argument("run_id", help="Run identifier")
	p_runs_show.add_argument("--workspace", help="Path to workspace folder (default: current directory)")
	p_runs_show.add_argument("--json", action="store_true", help="Output JSON")

	p_venv = sub.add_parser("venv-setup", help="Set up venv dependencies for Continuum")
	p_venv.add_argument("--profile", choices=["minimal", "ai"], default="ai", help="Requirements profile")
	p_venv.add_argument("--force", action="store_true", help="Overwrite requirements.txt if it exists")
	p_venv.add_argument("--no-install", action="store_true", help="Skip installing requirements")
	p_venv.add_argument("--smoke", action="store_true", help="Run a smoke check after setup")
	p_venv.add_argument("--emit", action="store_true", help="Emit shell snippet for: eval \"$(continuum venv-setup --emit)\"")

	p_doctor = sub.add_parser("doctor", help="Show Continuum environment info")
	p_doctor.add_argument("--workspace", help="Path to workspace folder (default: current directory)")

	p_status = sub.add_parser("status", help="Show workspace status")
	p_status.add_argument("--workspace", help="Path to workspace folder (default: current directory)")
	p_status.add_argument("--json", action="store_true", help="Output JSON")

	p_scan = sub.add_parser("scan", help="Scan workspace files")
	p_scan.add_argument("--workspace", help="Path to workspace folder (default: current directory)")
	p_scan.add_argument("--json", action="store_true", help="Output JSON")

	p_env = sub.add_parser("env", help="Show environment capability info")
	p_env.add_argument("--workspace", help="Path to workspace folder (default: current directory)")
	p_env.add_argument("--json", action="store_true", help="Output JSON")
	p_env.add_argument("--write", action="store_true", help="Write env artifact to workspace if possible")
	p_env.add_argument("--no-write", action="store_true", help="Do not write env artifact")

	p_ckpt = sub.add_parser("checkpoints", help="Manage checkpoints")
	p_ckpt_sub = p_ckpt.add_subparsers(dest="ckpt_cmd", required=True)

	p_ckpt_list = p_ckpt_sub.add_parser("list", help="List checkpoints")
	p_ckpt_list.add_argument("--workspace", help="Path to workspace folder (default: current directory)")
	p_ckpt_list.add_argument("--json", action="store_true", help="Output JSON")

	p_ckpt_latest = p_ckpt_sub.add_parser("latest", help="Show latest checkpoint")
	p_ckpt_latest.add_argument("--workspace", help="Path to workspace folder (default: current directory)")
	p_ckpt_latest.add_argument("--json", action="store_true", help="Output JSON")

	p_ckpt_prune = p_ckpt_sub.add_parser("prune", help="Prune old checkpoints")
	p_ckpt_prune.add_argument("--keep", type=int, required=True, help="Number of newest checkpoints to keep")
	p_ckpt_prune.add_argument("--workspace", help="Path to workspace folder (default: current directory)")
	p_ckpt_prune.add_argument("--dry-run", action="store_true", help="Show what would be deleted")

	p_train = sub.add_parser("train", help="Launch training script")
	p_train.add_argument("--workspace", help="Path to workspace folder (default: current directory)")
	p_train.add_argument("--script", required=True, help="Path to python script to launch")
	p_train.add_argument("--backend", choices=["accelerate", "torchrun", "python"], default="accelerate", help="Launcher backend")
	p_train.add_argument("--dry-run", action="store_true", help="Print command and exit")
	p_train.add_argument("passthrough", nargs=argparse.REMAINDER, help="Arguments after -- are passed to the script")

	p_infer = sub.add_parser("infer", help="Launch inference script")
	p_infer.add_argument("--workspace", help="Path to workspace folder (default: current directory)")
	p_infer.add_argument("--script", required=True, help="Path to python script to launch")
	p_infer.add_argument("--backend", choices=["auto", "vllm", "transformers", "python"], default="auto", help="Backend selector")
	p_infer.add_argument("--dry-run", action="store_true", help="Print command and exit")
	p_infer.add_argument("passthrough", nargs=argparse.REMAINDER, help="Arguments after -- are passed to the script")

	p_install = sub.add_parser("install", help="Install tools and bundles")
	p_install.add_argument("--workspace", help="Path to workspace folder (default: current directory)")
	p_install.add_argument("--yes", action="store_true", help="Auto-confirm installations")
	p_install.add_argument("--no-prompt", action="store_true", help="Auto-confirm installations")
	p_install.add_argument("--dry-run", action="store_true", help="Show what would be installed")
	p_install.add_argument("--debug", action="store_true", help="Show debug output")
	p_install.add_argument("--json", action="store_true", help="Output JSON (doctor only)")
	p_install.add_argument("target", nargs="?", help="Target: list | doctor | all | <installer/bundle>")

	p_pull = sub.add_parser("pull", help="Pull resources and models")
	p_pull.add_argument("--workspace", help="Path to workspace folder (default: current directory)")
	p_pull.add_argument("--yes", action="store_true", help="Auto-confirm pulls")
	p_pull.add_argument("--no-prompt", action="store_true", help="Auto-confirm pulls")
	p_pull.add_argument("--dry-run", action="store_true", help="Show what would be pulled")
	p_pull.add_argument("--debug", action="store_true", help="Show debug output")
	p_pull.add_argument("--json", action="store_true", help="Output JSON (doctor only)")
	p_pull.add_argument("target", nargs="?", help="Target: list | doctor | all | <pull target>")

	p_create = sub.add_parser("create", help="Create models and bundles")
	p_create.add_argument("--workspace", help="Path to workspace folder (default: current directory)")
	p_create.add_argument("--yes", action="store_true", help="Auto-confirm creates")
	p_create.add_argument("--no-prompt", action="store_true", help="Auto-confirm creates")
	p_create.add_argument("--dry-run", action="store_true", help="Show what would be created")
	p_create.add_argument("--debug", action="store_true", help="Show debug output")
	p_create.add_argument("--json", action="store_true", help="Output JSON (doctor only)")
	p_create.add_argument("target", nargs="?", help="Target: list | doctor | all | <create target>")

	p_engine = sub.add_parser("engine", help="Run the Data Engine")
	p_engine.add_argument("--workspace", help="Path to workspace folder (default: current directory)")
	p_engine.add_argument("--debug", action="store_true", help="Show debug output")
	p_engine.add_argument("passthrough", nargs=argparse.REMAINDER, help="Arguments after -- are passed to run_all.py")
	
	return parser


def main(argv: list[str] | None = None) -> int:
	parser = build_parser()
	args = parser.parse_args(argv)

	if args.cmd == "init":
		ws = Path(args.workspace).expanduser().resolve() if args.workspace else Path.cwd().resolve()
		try:
			if not ws.exists():
				raise FileNotFoundError(f"Workspace path does not exist: {ws}")
			if not ws.is_dir():
				raise NotADirectoryError(f"Workspace path is not a directory: {ws}")
			init_workspace(ws)
			try:
				run = create_run(ws, command="init")
				finish_run(run, "success")
				print(f"[run] {run.run_id}")
			except Exception as e:
				print(f"[warn] Run logging failed: {e}")
			print(f"[ok] Workspace initialized at: {ws}")
			return 0
		except Exception as e:
			print(f"[err] {e}")
			return 1
	
	if args.cmd == "runs":
		ws = Path(args.workspace).expanduser().resolve() if getattr(args, "workspace", None) else Path.cwd().resolve()
		if args.runs_cmd == "list":
			try:
				ensure_workspace(ws, require_init=True)
				entries = []
				for run_dir in list_runs(ws):
					try:
						meta = read_run_meta(run_dir)
						entries.append({
							"run_id": meta.get("run_id"),
							"status": meta.get("status"),
							"command": meta.get("command"),
							"started_at": meta.get("started_at"),
							"finished_at": meta.get("finished_at"),
							"workspace": meta.get("workspace"),
							"stdout_path": meta.get("stdout_path"),
							"stderr_path": meta.get("stderr_path"),
							"error": None,
						})
					except Exception as e:
						entries.append({
							"run_id": run_dir.name,
							"status": "corrupt",
							"command": None,
							"started_at": None,
							"finished_at": None,
							"workspace": None,
							"stdout_path": None,
							"stderr_path": None,
							"error": str(e),
						})
				if args.json:
					import json
					print(json.dumps(entries, indent=2))
				else:
					print("RUN_ID\tSTATUS\tCOMMAND\tSTARTED_AT")
					for meta in entries:
						run_id = meta.get("run_id", "unknown")
						status = meta.get("status", "unknown")
						command = meta.get("command", "unknown")
						started = meta.get("started_at", "unknown")
						print(f"{run_id}\t{status}\t{command}\t{started}")
				return 0
			except Exception as e:
				print(f"[err] {e}")
				return 1
		if args.runs_cmd == "show":
			try:
				ensure_workspace(ws, require_init=True)
				run_dir = ws / ".continuum" / "runs" / args.run_id
				if not run_dir.exists() or not run_dir.is_dir():
					raise FileNotFoundError(f"Run not found: {args.run_id}")
				meta = read_run_meta(run_dir)
				stdout_path = meta.get("stdout_path")
				stderr_path = meta.get("stderr_path")
				if args.json:
					import json
					out = dict(meta)
					print(json.dumps(out, indent=2))
				else:
					print(f"run_id: {meta.get('run_id')}")
					print(f"status: {meta.get('status')}")
					print(f"command: {meta.get('command')}")
					print(f"workspace: {meta.get('workspace')}")
					print(f"started_at: {meta.get('started_at')}")
					print(f"finished_at: {meta.get('finished_at')}")
					print(f"stdout_path: {stdout_path if stdout_path else 'missing'}")
					print(f"stderr_path: {stderr_path if stderr_path else 'missing'}")
				return 0
			except Exception as e:
				print(f"[err] {e}")
				return 1
	
	if args.cmd == "venv-setup":
		if args.emit:
			snippet = (
				'command -v python3 >/dev/null 2>&1 || { echo "python3 not found" >&2; return 1; }\n'
				'[ -d ".venv" ] || python3 -m venv .venv\n'
				'. ".venv/bin/activate"\n'
			)
			print(snippet, end="")
			return 0
		if not ensure_venv_active():
			print("[err] No active virtual environment detected.")
			print("Run:")
			print("  python -m venv .venv")
			print("  source .venv/bin/activate")
			return 1
		try:
			py = sys.executable
			print(f"[info] Using Python: {py}")
			run_cmd([py, "-m", "pip", "install", "-U", "pip", "setuptools", "wheel"])
			repo_root = repo_root_from_here()
			run_cmd([py, "-m", "pip", "install", "-e", str(repo_root / "engine")])
			req_path = Path.cwd() / "requirements.txt"
			if not req_path.exists() or args.force:
				generate_requirements(req_path, profile=args.profile)
				print(f"[ok] Wrote requirements: {req_path}")
			else:
				print(f"[ok] Using existing requirements: {req_path}")
			if not args.no_install:
				if args.profile == "ai":
					print("[warn] AI profile installs torch. CUDA/driver mismatches may require manual install.")
				run_cmd([py, "-m", "pip", "install", "-r", str(req_path)])
			if args.smoke:
				run_cmd(["continuum", "--help"])
				print("[ok] Smoke check passed.")
			return 0
		except Exception as e:
			print(f"[err] {e}")
			return 1
	
	if args.cmd == "doctor":
		ws = Path(args.workspace).expanduser().resolve() if args.workspace else Path.cwd().resolve()
		print(f"python: {sys.executable}")
		print(f"VIRTUAL_ENV: {os.environ.get('VIRTUAL_ENV') or 'not set'}")
		print(f"workspace: {ws}")
		try:
			ws_exists = ws.exists()
			ws_is_dir = ws.is_dir()
		except Exception as e:
			print(f"[err] Workspace check failed: {e}")
			return 1
		print(f"workspace_exists: {ws_exists}")
		print(f"workspace_is_dir: {ws_is_dir}")
		cont = ws / ".continuum"
		print(f".continuum_exists: {cont.exists()}")
		for name in ["runs", "logs", "cache", "state"]:
			p = cont / name
			print(f".continuum/{name}: {'ok' if p.exists() else 'missing'}")
		cfg = ws / "continuum.yaml"
		print(f"continuum.yaml: {cfg.exists()}")
		run_count = 0
		if cont.exists() and (cont / "runs").is_dir():
			try:
				run_count = len(list_runs(ws))
			except Exception as e:
				print(f"[err] Run count failed: {e}")
		print(f"run_count: {run_count}")
		return 0

	if args.cmd == "status":
		ws = Path(args.workspace).expanduser().resolve() if args.workspace else Path.cwd().resolve()
		try:
			ensure_workspace(ws, require_init=True)
		except Exception:
			print("Not a Continuum workspace. Run `continuum init`.")
			return 1
		entries = []
		try:
			entries = [read_run_meta(p) for p in list_runs(ws)]
		except Exception:
			entries = []
		latest = entries[0] if entries and isinstance(entries[0], dict) else None
		out = {
			"workspace": str(ws),
			"initialized": True,
			"run_count": len(entries),
			"latest_run": {
				"run_id": latest.get("run_id"),
				"status": latest.get("status"),
				"command": latest.get("command"),
				"started_at": latest.get("started_at"),
			} if latest else None,
		}
		if args.json:
			print(json.dumps(out, indent=2))
		else:
			print(f"workspace: {out['workspace']}")
			print(f"initialized: {out['initialized']}")
			print(f"run_count: {out['run_count']}")
			if out["latest_run"]:
				lr = out["latest_run"]
				print(f"latest_run: {lr['run_id']} {lr['status']} {lr['command']} {lr['started_at']}")
			else:
				print("latest_run: none")
		return 0

	if args.cmd == "scan":
		ws = Path(args.workspace).expanduser().resolve() if args.workspace else Path.cwd().resolve()
		try:
			ensure_workspace(ws, require_init=True)
		except Exception as e:
			print(f"[err] {e}")
			return 1
		run = None
		try:
			run = create_run(ws, command="scan")
			total_files = 0
			total_bytes = 0
			ext_counts: dict[str, int] = {}
			exclude = {".continuum", ".git", ".venv"}
			for root, dirs, files in os.walk(ws):
				root_path = Path(root)
				if root_path.name in exclude:
					dirs[:] = []
					continue
				dirs[:] = [d for d in dirs if d not in exclude]
				for name in files:
					path = root_path / name
					try:
						stat = path.stat()
					except Exception:
						continue
					total_files += 1
					total_bytes += stat.st_size
					suffix = path.suffix.lower().lstrip(".")
					ext = suffix if suffix else "<none>"
					ext_counts[ext] = ext_counts.get(ext, 0) + 1
			top_ext = dict(sorted(ext_counts.items(), key=lambda kv: kv[1], reverse=True)[:20])
			result = {
				"workspace": str(ws),
				"total_files": total_files,
				"total_bytes": total_bytes,
				"extension_counts": top_ext,
			}
			state_path = ws / ".continuum" / "state" / "scan.json"
			state_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
			finish_run(run, "success")
			if args.json:
				print(json.dumps(result, indent=2))
			return 0
		except Exception as e:
			if run is not None:
				try:
					finish_run(run, "failed")
				except Exception:
					pass
			print(f"[err] {e}")
			return 1
	
	if args.cmd == "env":
		ws = Path(args.workspace).expanduser().resolve() if args.workspace else Path.cwd().resolve()
		if not ws.exists():
			print(f"[err] Workspace path does not exist: {ws}")
			return 1
		if not ws.is_dir():
			print(f"[err] Workspace path is not a directory: {ws}")
			return 1
		try:
			disk = shutil.disk_usage(ws)
		except Exception as e:
			print(f"[err] {e}")
			return 1

		ram_total = None
		ram_free = None
		try:
			import psutil  # type: ignore
			vm = psutil.virtual_memory()
			ram_total = int(vm.total)
			ram_free = int(vm.available)
		except Exception:
			pass

		torch_info: dict = {
			"installed": False,
			"version": None,
			"cuda_version": None,
			"cudnn_version": None,
			"cuda_available": False,
			"device_count": 0,
			"gpus": [],
			"error": None,
		}
		try:
			import torch  # type: ignore
			torch_info["installed"] = True
			torch_info["version"] = getattr(torch, "__version__", None)
			torch_info["cuda_version"] = getattr(torch.version, "cuda", None)
			try:
				torch_info["cudnn_version"] = torch.backends.cudnn.version()
			except Exception:
				torch_info["cudnn_version"] = None
			try:
				torch_info["cuda_available"] = bool(torch.cuda.is_available())
				torch_info["device_count"] = int(torch.cuda.device_count())
				gpus = []
				for i in range(torch_info["device_count"]):
					prop = torch.cuda.get_device_properties(i)
					gpus.append({
						"name": getattr(prop, "name", None),
						"total_memory_bytes": int(getattr(prop, "total_memory", 0)),
						"capability": f"{prop.major}.{prop.minor}" if hasattr(prop, "major") else None,
					})
				torch_info["gpus"] = gpus
			except Exception as e:
				torch_info["error"] = str(e)
		except Exception as e:
			torch_info["error"] = str(e)

		def _lib_info(mod: str) -> dict:
			try:
				m = __import__(mod)
				return {
					"installed": True,
					"version": getattr(m, "__version__", None),
					"error": None,
				}
			except Exception as e:
				return {
					"installed": False,
					"version": None,
					"error": str(e),
				}

		env = {
			"python": {
				"executable": sys.executable,
				"version": sys.version,
				"platform": platform.platform(),
			},
			"venv": {
				"virtual_env": os.environ.get("VIRTUAL_ENV"),
			},
			"hardware": {
				"cpu_count": os.cpu_count(),
				"ram_total_bytes": ram_total,
				"ram_free_bytes": ram_free,
				"disk_total_bytes": int(disk.total),
				"disk_free_bytes": int(disk.free),
			},
			"torch": torch_info,
			"optional_libs": {
				"xformers": _lib_info("xformers"),
				"flash_attn": _lib_info("flash_attn"),
				"triton": _lib_info("triton"),
				"bitsandbytes": _lib_info("bitsandbytes"),
				"vllm": _lib_info("vllm"),
			},
		}

		initialized = (ws / ".continuum").exists()
		want_write = (initialized and not args.no_write) or (not initialized and args.write and not args.no_write)
		if want_write:
			state_dir = ws / ".continuum" / "state"
			if state_dir.exists() and state_dir.is_dir():
				out_path = state_dir / "env.json"
				out_path.write_text(json.dumps(env, indent=2), encoding="utf-8")
				print(f"[ok] Wrote env artifact: {out_path}")

		if args.json:
			print(json.dumps(env, indent=2))
			return 0

		top_gpu_name = None
		top_gpu_mem = None
		if torch_info.get("gpus"):
			g0 = max(torch_info["gpus"], key=lambda g: g.get("total_memory_bytes") or 0)
			top_gpu_name = g0.get("name")
			top_gpu_mem = g0.get("total_memory_bytes")
		print(f"python: {sys.executable}")
		print(f"venv: {os.environ.get('VIRTUAL_ENV') or 'not set'}")
		print(f"torch_installed: {torch_info.get('installed')}")
		print(f"cuda_available: {torch_info.get('cuda_available')}")
		print(f"gpu_count: {torch_info.get('device_count')}")
		print(f"top_gpu: {top_gpu_name or 'n/a'}")
		print(f"top_gpu_mem_bytes: {top_gpu_mem if top_gpu_mem is not None else 'n/a'}")
		print(f"disk_free_bytes: {int(disk.free)}")
		print(f"ram_free_bytes: {ram_free if ram_free is not None else 'n/a'}")
		return 0
	
	if args.cmd == "checkpoints":
		ws = Path(args.workspace).expanduser().resolve() if getattr(args, "workspace", None) else Path.cwd().resolve()
		try:
			ensure_workspace(ws, require_init=True)
		except Exception as e:
			print(f"[err] {e}")
			return 1
		checkpoints_root = ws / "models" / "checkpoints"
		entries = []
		if checkpoints_root.exists() and checkpoints_root.is_dir():
			for p in checkpoints_root.iterdir():
				try:
					mtime = os.path.getmtime(p)
				except Exception:
					mtime = 0.0
				mtime_iso = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime)) if mtime else "unknown"
				size_bytes = 0
				if p.is_file():
					try:
						size_bytes = p.stat().st_size
					except Exception:
						size_bytes = 0
				elif p.is_dir():
					for root, _, files in os.walk(p):
						for name in files:
							fp = Path(root) / name
							try:
								size_bytes += fp.stat().st_size
							except Exception:
								continue
				entries.append({
					"name": p.name,
					"path": str(p),
					"is_dir": p.is_dir(),
					"mtime": mtime,
					"mtime_epoch": float(mtime),
					"mtime_iso": mtime_iso,
					"size_bytes": size_bytes,
				})
		entries.sort(key=lambda e: e.get("mtime", 0), reverse=True)

		if args.ckpt_cmd == "list":
			if args.json:
				print(json.dumps(entries, indent=2))
			else:
				print("NAME\tMTIME\tSIZE_MB\tTYPE")
				for e in entries:
					mtime_str = e.get("mtime_iso") or "unknown"
					size_mb = e["size_bytes"] / (1024 * 1024)
					typ = "dir" if e["is_dir"] else "file"
					print(f"{e['name']}\t{mtime_str}\t{size_mb:.2f}\t{typ}")
			return 0

		if args.ckpt_cmd == "latest":
			if not entries:
				if args.json:
					print("null")
				else:
					print("latest: none")
				return 0
			latest = entries[0]
			if args.json:
				print(json.dumps(latest, indent=2))
			else:
				mtime_str = latest.get("mtime_iso") or "unknown"
				size_mb = latest["size_bytes"] / (1024 * 1024)
				typ = "dir" if latest["is_dir"] else "file"
				print(f"latest: {latest['name']} {mtime_str} {size_mb:.2f}MB {typ}")
			return 0

		if args.ckpt_cmd == "prune":
			keep = max(args.keep, 0)
			to_keep = entries[:keep]
			to_delete = entries[keep:]
			if args.dry_run:
				print(f"would_delete_count: {len(to_delete)}")
				print(f"kept_count: {len(to_keep)}")
				return 0
			try:
				checkpoints_root_resolved = checkpoints_root.resolve(strict=False)
			except Exception:
				try:
					checkpoints_root_resolved = checkpoints_root.resolve()
				except Exception:
					checkpoints_root_resolved = checkpoints_root
			deleted_count = 0
			for e in to_delete:
				p = Path(e["path"])
				try:
					p_resolved = p.resolve()
				except Exception:
					print(f"[err] Refusing to delete outside checkpoints_root: {p}")
					continue
				if not str(p_resolved).startswith(str(checkpoints_root_resolved) + os.sep) and p_resolved != checkpoints_root_resolved:
					print(f"[err] Refusing to delete outside checkpoints_root: {p}")
					continue
				try:
					if p.is_dir():
						shutil.rmtree(p)
					else:
						p.unlink()
					deleted_count += 1
				except Exception as ex:
					print(f"[err] Failed to delete {p}: {ex}")
			print(f"deleted_count: {deleted_count}")
			print(f"kept_count: {len(to_keep)}")
			return 0
	
	if args.cmd == "train":
		ws = Path(args.workspace).expanduser().resolve() if args.workspace else Path.cwd().resolve()
		try:
			ensure_workspace(ws, require_init=True)
		except Exception as e:
			print(f"[err] {e}")
			return 1
		script_path = Path(args.script).expanduser()
		if not script_path.is_absolute():
			script_path = (Path.cwd() / script_path).resolve()
		if not script_path.exists():
			print(f"[err] Script not found: {script_path}")
			return 1
		passthrough = args.passthrough
		if passthrough and passthrough[0] == "--":
			passthrough = passthrough[1:]
		cmd = []
		if args.backend == "accelerate":
			if shutil.which("accelerate"):
				cmd = ["accelerate", "launch", str(script_path), *passthrough]
			else:
				print("[warn] accelerate not found, falling back to python backend.")
				cmd = [sys.executable, str(script_path), *passthrough]
		elif args.backend == "torchrun":
			try:
				import torch  # type: ignore
			except Exception:
				print("[err] torch not installed")
				return 1
			cmd = [sys.executable, "-m", "torch.distributed.run", str(script_path), *passthrough]
		else:
			cmd = [sys.executable, str(script_path), *passthrough]

		if args.dry_run:
			print(shlex.join(cmd))
			return 0
		run = None
		try:
			run = create_run(ws, command="train")
		except Exception as e:
			print(f"[warn] Run logging failed: {e}")
		try:
			result = subprocess.run(cmd)
		except KeyboardInterrupt:
			if run is not None:
				finish_run(run, "failed")
			return 130
		except Exception as e:
			if run is not None:
				finish_run(run, "failed")
			print(f"[err] {e}")
			return 1
		if run is not None:
			if result.returncode == 0:
				finish_run(run, "success")
			else:
				finish_run(run, "failed")
		return result.returncode
	
	if args.cmd == "infer":
		ws = Path(args.workspace).expanduser().resolve() if args.workspace else Path.cwd().resolve()
		try:
			ensure_workspace(ws, require_init=True)
		except Exception as e:
			print(f"[err] {e}")
			return 1
		script_path = Path(args.script).expanduser()
		if not script_path.is_absolute():
			script_path = (Path.cwd() / script_path).resolve()
		if not script_path.exists():
			print(f"[err] Script not found: {script_path}")
			return 1
		passthrough = args.passthrough
		if passthrough and passthrough[0] == "--":
			passthrough = passthrough[1:]

		selected = args.backend
		if selected == "auto":
			try:
				import vllm  # type: ignore
				import torch  # type: ignore
				if torch.cuda.is_available():
					selected = "vllm"
				else:
					selected = "transformers"
			except Exception:
				try:
					import transformers  # type: ignore
					selected = "transformers"
				except Exception:
					selected = "python"
		elif selected == "vllm":
			try:
				import vllm  # type: ignore
				import torch  # type: ignore
				if not torch.cuda.is_available():
					print("[err] torch.cuda.is_available() is false")
					return 1
			except Exception:
				print("[err] vllm/torch not installed or unavailable")
				return 1
		elif selected == "transformers":
			try:
				import transformers  # type: ignore
			except Exception:
				print("[err] transformers not installed")
				return 1

		cmd = [sys.executable, str(script_path), *passthrough]

		if args.dry_run:
			print(f"backend: {selected}")
			print(shlex.join(cmd))
			return 0

		run = None
		try:
			run = create_run(ws, command="infer")
		except Exception as e:
			print(f"[warn] Run logging failed: {e}")
		try:
			result = subprocess.run(cmd)
		except KeyboardInterrupt:
			if run is not None:
				finish_run(run, "failed")
			return 130
		except Exception as e:
			if run is not None:
				finish_run(run, "failed")
			print(f"[err] {e}")
			return 1
		if run is not None:
			if result.returncode == 0:
				finish_run(run, "success")
			else:
				finish_run(run, "failed")
		return result.returncode
	
	if args.cmd == "install":
		ws = Path(args.workspace).expanduser().resolve() if args.workspace else Path.cwd().resolve()
		if not ws.exists():
			print(f"[err] Workspace path does not exist: {ws}")
			return 1
		if not ws.is_dir():
			print(f"[err] Workspace path is not a directory: {ws}")
			return 1
		ctx = InstallContext(workspace=ws, dry_run=args.dry_run, debug=args.debug, yes=args.yes or args.no_prompt)
		if not args.target:
			print("[err] Missing install target. Use `continuum install list` to see options.")
			return 1
		if args.json and args.target != "doctor":
			print("[err] --json is only valid with `continuum install doctor`.")
			return 1
		if args.target == "list":
			list_targets()
			return 0
		if args.target == "doctor":
			return run_doctor(ctx, json_output=args.json)
		if args.target == "all":
			return install_target("full", ctx)
		return install_target(args.target, ctx)
	
	if args.cmd == "pull":
		ws = Path(args.workspace).expanduser().resolve() if args.workspace else Path.cwd().resolve()
		if not ws.exists():
			print(f"[err] Workspace path does not exist: {ws}")
			return 1
		if not ws.is_dir():
			print(f"[err] Workspace path is not a directory: {ws}")
			return 1
		ctx = PullContext(workspace=ws, dry_run=args.dry_run, debug=args.debug, yes=args.yes or args.no_prompt)
		if not args.target:
			print("[err] Missing pull target. Use `continuum pull list` to see options.")
			return 1
		if args.json and args.target != "doctor":
			print("[err] --json is only valid with `continuum pull doctor`.")
			return 1
		if args.target == "list":
			list_pull_targets()
			return 0
		if args.target == "doctor":
			return run_pull_doctor(ctx, json_output=args.json)
		if args.target == "all":
			return pull_target("data_models", ctx)
		return pull_target(args.target, ctx)
	
	if args.cmd == "create":
		ws = Path(args.workspace).expanduser().resolve() if args.workspace else Path.cwd().resolve()
		if not ws.exists():
			print(f"[err] Workspace path does not exist: {ws}")
			return 1
		if not ws.is_dir():
			print(f"[err] Workspace path is not a directory: {ws}")
			return 1
		ctx = CreateContext(workspace=ws, dry_run=args.dry_run, debug=args.debug, yes=args.yes or args.no_prompt)
		if not args.target:
			print("[err] Missing create target. Use `continuum create list` to see options.")
			return 1
		if args.json and args.target != "doctor":
			print("[err] --json is only valid with `continuum create doctor`.")
			return 1
		if args.target == "list":
			list_create_targets()
			return 0
		if args.target == "doctor":
			return run_create_doctor(ctx, json_output=args.json)
		if args.target == "all":
			return create_target("engine", ctx)
		return create_target(args.target, ctx)
	
	if args.cmd == "engine":
		ws = Path(args.workspace).expanduser().resolve() if args.workspace else Path.cwd().resolve()
		if not ws.exists():
			print(f"[err] Workspace path does not exist: {ws}")
			return 1
		if not ws.is_dir():
			print(f"[err] Workspace path is not a directory: {ws}")
			return 1
		run_all_a = ws / "external" / "Model_Data-1O" / "app" / "run_all.py"
		run_all_b = ws / "external" / "model_data_1o" / "app" / "run_all.py"
		run_all = run_all_a if run_all_a.exists() else run_all_b if run_all_b.exists() else None
		if run_all is None:
			print("[err] Data engine not found. Expected run_all.py under external/Model_Data-1O/app or external/model_data_1o/app")
			return 1
		if shutil.which("python3") is None:
			print("[err] python3 not found. Run `continuum install base`.")
			return 1
		passthrough = args.passthrough
		if passthrough and passthrough[0] == "--":
			passthrough = passthrough[1:]
		print(f"Running data engine: {run_all}")
		try:
			result = subprocess.run(["python3", str(run_all)] + passthrough)
			return result.returncode
		except Exception as e:
			if args.debug:
				print(traceback.format_exc())
				return 1
			print(f"[err] {e}")
			return 1

	parser.print_help()
	return 1

if __name__ == "__main__":
	raise SystemExit(main())
