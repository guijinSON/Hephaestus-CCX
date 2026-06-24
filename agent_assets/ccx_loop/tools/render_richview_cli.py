#!/usr/bin/env python3
"""CLI wrapper for Hephaestus rich-view rendering.

This script is copied into isolated Codex/Claude attempt workspaces. It imports
the real renderer from the Hephaestus checkout via --repo-root or the
HEPHAESTUS_REPO_ROOT environment variable.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path


def maybe_reexec_with_rich_view_python() -> None:
    """Use the CAD-rendering env when the current Python lacks ocp-vscode."""
    if os.environ.get("HEPHAESTUS_RICH_VIEW_REEXECED") == "1":
        return
    if importlib.util.find_spec("ocp_vscode") is not None:
        return

    candidates: list[Path] = []
    configured = os.environ.get("HEPHAESTUS_RICH_VIEW_PYTHON")
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.append(Path("/opt/anaconda3/envs/Hepha/bin/python"))

    current = Path(sys.executable).resolve()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if not resolved.exists() or resolved == current:
            continue
        env = os.environ.copy()
        env["HEPHAESTUS_RICH_VIEW_REEXECED"] = "1"
        os.execvpe(str(resolved), [str(resolved), str(Path(__file__).resolve()), *sys.argv[1:]], env)


def resolve_repo_root(raw: str | None) -> Path:
    candidate = raw or os.environ.get("HEPHAESTUS_REPO_ROOT")
    if not candidate:
        raise SystemExit("ERROR: provide --repo-root or set HEPHAESTUS_REPO_ROOT")
    root = Path(candidate).expanduser().resolve()
    if not (root / "tools" / "tools.py").exists():
        raise SystemExit(f"ERROR: repo root does not contain tools/tools.py: {root}")
    return root


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--step-path", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--target-type", choices=["part", "assembly"], default="part")
    parser.add_argument("--target-id", required=True)
    parser.add_argument("--attempt", type=int, default=0)
    args = parser.parse_args()

    maybe_reexec_with_rich_view_python()

    repo_root = resolve_repo_root(args.repo_root)
    sys.path.insert(0, str(repo_root))

    from tools.tools import render_richview_result

    result = render_richview_result(
        step_path=args.step_path,
        output_dir=args.output_dir,
        prefix=args.prefix,
        target_type=args.target_type,
        target_id=args.target_id,
        attempt=args.attempt,
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0 if result.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
