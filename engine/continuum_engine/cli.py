from __future__ import annotations

import argparse
from pathlib import Path

from continuum_engine.workspace.layout import init_workspace
from continuum_engine.workspace.validate import ensure_workspace
from continuum_engine.runs.manager import create_run, finish_run, list_runs, read_run_meta


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

	parser.print_help()
	return 1

if __name__ == "__main__":
	raise SystemExit(main())
