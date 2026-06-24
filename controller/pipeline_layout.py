# pipeline_layout.py — Shared pipeline orchestration helpers
#
# Provides generic layout, topic sanitization, and output resolution
# for all pipeline agents. Adding a new agent requires no changes here —
# just call agent_layout() with the agent name.

from __future__ import annotations

import re
from pathlib import Path
from typing import TypedDict


class AgentLayout(TypedDict):
    root: Path
    temp: Path
    final: Path
    permanent: Path


def sanitize_topic(text: str) -> str:
    """Derive a stable topic token from free-form text.

    Rules: lowercase, alphanumeric + underscore, max 40 chars,
    no leading/trailing underscores.
    """
    slug = text.lower()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    slug = slug.strip("_")
    return slug[:40]


def agent_layout(artifact_root: str | Path, agent_name: str) -> AgentLayout:
    """Return the standard directory layout for a pipeline agent.

    Layout::

        artifacts/<topic>/<agent_name>/
        ├── temp/        Backend root — all write_file calls land here
        ├── final/       Published outputs for downstream agents
        └── permanent/   Reserved (empty by policy)
    """
    base = Path(artifact_root) / agent_name
    return AgentLayout(
        root=base,
        temp=base / "temp",
        final=base / "final",
        permanent=base / "permanent",
    )


def resolve_output(search_dir: Path, filename: str) -> Path | None:
    """Locate an agent output file, handling virtual-path nesting.

    ``FilesystemBackend(virtual_mode=True)`` maps absolute-looking paths
    under ``root_dir``.  When the LLM writes to e.g.
    ``/workspace/hexapod/design_brief.md``, the file ends up at
    ``<root_dir>/workspace/hexapod/design_brief.md`` on disk.

    Search order (deterministic):
      1. ``search_dir/filename``               — bare filename (correct behavior)
      2. ``search_dir/workspace/filename``      — common LLM virtual path
      3. ``search_dir/workspace/*/filename``    — topic-prefixed virtual path
      4. Recursive fallback                     — raise on ambiguity

    Returns:
        Path to the file, or ``None`` if not found anywhere.

    Raises:
        FileNotFoundError: If more than one candidate is found in the
            recursive fallback (ambiguous match).
    """
    # 1. Direct
    direct = search_dir / filename
    if direct.exists():
        return direct

    # 2. workspace/
    ws = search_dir / "workspace" / filename
    if ws.exists():
        return ws

    # 3. workspace/*/
    ws_dir = search_dir / "workspace"
    if ws_dir.exists():
        ws_topic = list(ws_dir.glob(f"*/{filename}"))
        if len(ws_topic) == 1:
            return ws_topic[0]
        if len(ws_topic) > 1:
            raise FileNotFoundError(
                f"Ambiguous: found {len(ws_topic)} copies of '{filename}' "
                f"under {ws_dir}: {', '.join(str(c) for c in ws_topic)}"
            )

    # 4. Recursive fallback — fail on ambiguity
    candidates = list(search_dir.rglob(filename))
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise FileNotFoundError(
            f"Ambiguous: found {len(candidates)} copies of '{filename}' "
            f"under {search_dir}: {', '.join(str(c) for c in candidates)}"
        )

    return None
