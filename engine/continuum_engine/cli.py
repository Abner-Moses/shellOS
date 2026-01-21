from __future__ import annotations

import argparse
from pathlib import Path

from continuum_engine.workspace.layout import init_workspace
from continuum_engine.runs.manager import create_run, finish_run


def build_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(prog="continuum")
	sub = parser.add_subparsers(dest="cmd", required=True)
	
	p_init = sub.add_parser("init", help="Initialize a Continuum workspace")
	p_init.add_argument("--workspace", required=True, help="Path to workspace folder")
	
	return parser


def main(argv: list[str] | None = None) -> int:
	parser = build_parser()
	args = parser.parse_args(argv)

	if args.cmd == "init":
		ws = Path(args.workspace).expanduser().resolve()
		run = create_run(ws, command="init")
		
		try:
			init_workspace(ws)
			finish_run(run, "success")
			print(f"[ok] Workspace initialized at: {ws}")
			print(f"[run] {run.run_id}")
			return 0
		except Exception as e:
			finish_run(run, "failed")
			print(f"[err] {e}")
			print(f"[run] {run.run_id}")
			return 1

	parser.print_help()
	return 1

if __name__ == "__main__":
	raise SystemExit(main())
