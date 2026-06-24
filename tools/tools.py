"""Pipeline tools for Hephaestus.

Shared tools (available to both research and CAD phases):
    short_search       — Fast web search via OpenAI search API
    internet_search    — Canonical alias for short_search in the new pipeline
    deep_research      — Comprehensive research via Gemini Deep Research API
    think_tool         — Strategic reflection checkpoint
    ask_user           — Ask the user a question and wait for a reply

CAD-phase tools (Phase 2 only):
    run_cadquery_script    — Execute a CadQuery model.py and validate STEP export
    render_7view           — Render the standard 7-view OCP camera set to PNG
    render_12view          — Render the profile-selected 12-view OCP camera set to PNG
    render_richview        — Render the richer 21-view OCP camera set to PNG (18 opaque + 3 x-ray)
    check_assembly_interference — Check an assembly STEP for overlap / clearance issues
    render_iso_orbit       — Experimental multi-direction iso renders of a STEP file
"""

from __future__ import annotations

import base64
import contextlib
import json
import math
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import yaml
from google import genai
try:
    from langchain.tools import ToolRuntime
    from langchain_core.tools import InjectedToolArg, tool
    from langgraph.types import interrupt
except ImportError:
    # Rendering helpers are used in lightweight isolated attempt directories.
    # Those paths do not need LangChain tool registration, and some local
    # environments have an incomplete tenacity install that breaks LangChain
    # imports. Keep result-returning render functions importable anyway.
    class ToolRuntime:  # type: ignore[no-redef]
        pass

    class InjectedToolArg:  # type: ignore[no-redef]
        pass

    def tool(*args, **_kwargs):  # type: ignore[no-redef]
        if args and callable(args[0]):
            return args[0]

        def decorator(func):
            return func

        return decorator

    def interrupt(_payload):  # type: ignore[no-redef]
        raise RuntimeError("langgraph interrupt is unavailable")
try:
    from litellm import completion
except ImportError:
    def completion(*_args, **_kwargs):  # type: ignore[no-redef]
        raise RuntimeError("litellm is unavailable")
from typing_extensions import Annotated, Literal

from controller.pipeline_layout import agent_layout


# ── Shared tools ─────────────────────────────────────────────────────────────

_CAD_PIPELINE_AGENTS = frozenset({
    "cad_gen_agent",
    "cg1",
    "cgj1",
    "cgr1",
    "ca1",
    "caj1",
})


def _cad_temp_dir_from_runtime(
    runtime: ToolRuntime | None,
) -> Path | None:
    state = getattr(runtime, "state", None) or {}
    if not isinstance(state, dict):
        return None

    artifact_root = state.get("artifact_root")
    if not artifact_root:
        return None

    pipeline_agent = str(state.get("pipeline_agent") or "")
    cad_temp_dir = agent_layout(artifact_root, "cad_gen_agent")["temp"]
    if pipeline_agent and pipeline_agent not in _CAD_PIPELINE_AGENTS and not cad_temp_dir.exists():
        return None
    try:
        return cad_temp_dir.resolve(strict=False)
    except OSError:
        return cad_temp_dir


def _resolve_pipeline_tool_path(
    path_str: str,
    runtime: ToolRuntime | None = None,
) -> Path:
    raw = (path_str or "").strip()
    path = Path(raw or ".").expanduser()
    cad_temp_dir = _cad_temp_dir_from_runtime(runtime)

    if cad_temp_dir is not None:
        normalized = raw.replace("\\", "/")
        should_map_virtual = False
        if not path.is_absolute():
            should_map_virtual = not normalized.startswith(("./", "../"))
        elif not path.exists():
            should_map_virtual = (
                normalized == "/"
                or normalized.startswith("/workspace/")
                or path.parent == Path("/")
            )

        if should_map_virtual:
            path = cad_temp_dir / normalized.lstrip("/")

    try:
        return path.resolve(strict=False)
    except OSError:
        return path

@tool(parse_docstring=True)
def short_search(
    query: str,
    search_context_size: Annotated[Literal["low", "medium", "high"], InjectedToolArg] = "medium",
    style: Annotated[Literal["brief", "bullet"], InjectedToolArg] = "brief",
) -> str:
    """Short web-search-backed answer using an OpenAI search model via LiteLLM.

    Uses Chat Completions with a search-capable model. The model performs web
    search and includes sources as URLs in its answer.

    Args:
        query: What to look up
        search_context_size: low/medium/high context pulled from search
        style: brief paragraph or bullet points

    Returns:
        Model answer including a Sources section with URLs (best-effort, prompted).
    """
    if not os.getenv("OPENAI_API_KEY"):
        return "Error: OPENAI_API_KEY is not set in the environment."

    formatting = (
        "Answer in 5-8 tight sentences."
        if style == "brief"
        else "Answer in 5-10 bullet points."
    )

    resp = completion(
        model="openai/gpt-5-search-api",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a careful research assistant. Use web search to verify facts. "
                    "Always include a 'Sources' section with the URLs you used."
                ),
            },
            {
                "role": "user",
                "content": f"{formatting}\n\nQuery: {query}",
            },
        ],
        web_search_options={"search_context_size": search_context_size},
    )

    try:
        return resp.choices[0].message.content
    except Exception:
        return "Research Failed!"


@tool(parse_docstring=True)
def internet_search(
    query: str,
    search_context_size: Annotated[Literal["low", "medium", "high"], InjectedToolArg] = "medium",
    style: Annotated[Literal["brief", "bullet"], InjectedToolArg] = "brief",
) -> str:
    """Canonical lightweight web-search tool for the refactored Research stage.

    This is a stable wrapper name over the current LiteLLM/OpenAI search path.
    It intentionally mirrors ``short_search`` so the new role/tool matrix can
    expose ``internet_search`` without depending on legacy tool names.

    Args:
        query: What to look up
        search_context_size: low/medium/high context pulled from search
        style: brief paragraph or bullet points

    Returns:
        Model answer including a Sources section with URLs (best-effort, prompted).
    """
    return short_search.invoke(
        {
            "query": query,
            "search_context_size": search_context_size,
            "style": style,
        }
    )


@tool(parse_docstring=True)
def deep_research(
    query: str,
    agent: Annotated[str, InjectedToolArg] = "deep-research-pro-preview-12-2025",
    max_wait_seconds: Annotated[float, InjectedToolArg] = 900.0,
) -> str:
    """Run Gemini Deep Research via the Interactions API and return the final result text.

    Fixed settings: background=True, poll_seconds=10.

    Args:
        query: Research prompt to run.
        agent: Deep Research agent name.
        max_wait_seconds: Stop polling after this many seconds.

    Returns:
        Final research text, or an error/timeout string.
    """
    if not os.getenv("GEMINI_API_KEY"):
        return "Error: GEMINI_API_KEY is not set in the environment."

    client = genai.Client()

    try:
        interaction = client.interactions.create(
            input=query,
            agent=agent,
            background=True,
        )
    except Exception as e:
        return f"Error starting research interaction: {e}"

    interaction_id = getattr(interaction, "id", None) or getattr(interaction, "name", None)
    if not interaction_id:
        return "Error: could not read interaction id from create() response."

    poll_seconds = 10.0
    start = time.time()

    while True:
        try:
            interaction = client.interactions.get(interaction_id)
        except Exception as e:
            return f"Error polling interaction {interaction_id}: {e}"

        status = getattr(interaction, "status", None) or "unknown"

        if status == "completed":
            outputs = getattr(interaction, "outputs", None) or []
            if not outputs:
                return f"Research completed but no outputs found (interaction {interaction_id})."
            final_text = (getattr(outputs[-1], "text", "") or "").strip()
            return final_text or f"Completed but final output was empty (interaction {interaction_id})."

        if status in {"failed", "cancelled", "canceled", "expired"}:
            err = getattr(interaction, "error", None)
            return f"Research {status} (interaction {interaction_id}). Error: {err}"

        if (time.time() - start) >= max_wait_seconds:
            return (
                f"Research still {status} after {max_wait_seconds}s (interaction {interaction_id}). "
                f"Poll later with client.interactions.get('{interaction_id}')."
            )

        time.sleep(poll_seconds)


@tool(parse_docstring=True)
def think_tool(reflection: str) -> str:
    """Tool for strategic reflection on research progress and decision-making.

    Use this tool after each search to analyze results and plan next steps
    systematically. Creates a deliberate pause for quality decision-making.

    Reflection should address:
    1. Analysis of current findings
    2. Gap assessment
    3. Quality evaluation
    4. Strategic decision on next steps

    Args:
        reflection: Your detailed reflection on research progress, findings, gaps, and next steps

    Returns:
        Confirmation that reflection was recorded for decision-making
    """
    return f"Reflection recorded: {reflection}"


def build_ask_user_tool(max_calls: int = 3):
    """Build an ask_user tool with a hard call-count limit.

    After ``max_calls`` invocations the tool rejects further calls so the
    agent is forced to proceed with whatever information it has.
    """
    calls_remaining = {"n": max_calls}

    @tool(parse_docstring=True)
    def ask_user(message: str) -> str:
        """Ask the user a question and wait for their reply.

        Use this when you need clarification or input that only the user can
        provide. The message should be conversational and specific — not a
        structured form. Group related questions into a single call.

        Args:
            message: The question(s) to present to the user.

        Returns:
            The user's free-text reply.
        """
        if calls_remaining["n"] <= 0:
            return (
                "ERROR: ask_user call limit reached. You must proceed with "
                "the information you already have. Do NOT call ask_user again."
            )
        calls_remaining["n"] -= 1
        response = interrupt({"type": "ask_user", "message": message})
        if isinstance(response, dict):
            return response.get("answer", str(response))
        return str(response)

    return ask_user


# ── CAD-phase tools ───────────────────────────────────────────────────────────

@tool(parse_docstring=True)
def run_cadquery_script(script_path: str) -> str:
    """Execute a CadQuery model.py script and return structured output.

    Runs the script in a subprocess from the script's directory and returns
    exit status, stdout, stderr, and whether a STEP file was produced. Relative
    ``Exported:`` paths are resolved against the script directory. Use this to
    validate and self-repair model.py files during CAD generation.

    Call this after every model.py write. If exit_code != 0, read stderr for
    the traceback, fix the specific error in model.py, and call this tool again.
    Only write status: complete when exit_code == 0, step_exists == true, and
    step_size_bytes > 0.

    Args:
        script_path: Absolute path to the model.py script to execute.

    Returns:
        Structured result with exit_code, step_exists, step_size_bytes, stdout,
        and stderr fields.
    """
    script = Path(script_path).expanduser()
    try:
        script = script.resolve(strict=False)
    except OSError:
        pass
    script_dir = script.parent if script.parent != Path("") else Path.cwd()

    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(script_dir),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return "exit_code: -1\nstep_exists: false\nstep_size_bytes: 0\nstdout:\n\nstderr:\nScript timed out after 120s"
    except Exception as e:
        return f"exit_code: -1\nstep_exists: false\nstep_size_bytes: 0\nstdout:\n\nstderr:\nFailed to launch script: {e}"

    step_path: Path | None = None
    for line in result.stdout.splitlines():
        if line.startswith("Exported:"):
            exported = Path(line.split(":", 1)[1].strip()).expanduser()
            step_path = exported if exported.is_absolute() else script_dir / exported
            try:
                step_path = step_path.resolve(strict=False)
            except OSError:
                pass
            break

    step_exists = bool(step_path and step_path.is_file())
    step_size = step_path.stat().st_size if step_exists and step_path else 0

    return (
        f"exit_code: {result.returncode}\n"
        f"step_path: {step_path or ''}\n"
        f"step_exists: {step_exists}\n"
        f"step_size_bytes: {step_size}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


# ── CAD render and interference helpers ────────────────────────────────────

_SEVEN_VIEW_NAMES = ("front", "rear", "left", "right", "top", "bottom", "iso")
_TWELVE_VIEW_NAMES = (
    *_SEVEN_VIEW_NAMES,
    "iso_front_right",
    "iso_rear_right",
    "iso_rear_left",
    "iso_front_left",
    "iso_top_front_right",
)
_RICH_VIEW_NAMES = (
    *_TWELVE_VIEW_NAMES,
    "front_close",
    "rear_close",
    "left_close",
    "right_close",
    "top_close",
    "iso_close",
    "iso_xray",
    "front_xray",
    "right_xray",
)
_VIEW_MODE_TO_ORDER = {
    "7view": _SEVEN_VIEW_NAMES,
    "12view": _TWELVE_VIEW_NAMES,
    "richview": _RICH_VIEW_NAMES,
}
_OCP_CAMERA_BY_VIEW = {
    "front": "FRONT",
    "rear": "BACK",
    "left": "LEFT",
    "right": "RIGHT",
    "top": "TOP",
    "bottom": "BOTTOM",
    "iso": "ISO",
}
_OCP_RENDER_LOCK = Path("/tmp/hephaestus_ocp_render.lock")
_ISO_ORBIT_VIEW_NAMES = (
    "iso_front_right",
    "iso_rear_right",
    "iso_rear_left",
    "iso_front_left",
)
_CUSTOM_VIEW_VECTOR_BY_NAME = {
    "iso_front_right": (1.0, -1.0, 1.0),
    "iso_rear_right": (1.0, 1.0, 1.0),
    "iso_rear_left": (-1.0, 1.0, 1.0),
    "iso_front_left": (-1.0, -1.0, 1.0),
    "iso_top_front_right": (1.0, -1.0, 2.0),
}
_RICH_VIEW_PRESET_BY_NAME: dict[str, dict[str, object]] = {
    "front": {"camera": "FRONT"},
    "rear": {"camera": "BACK"},
    "left": {"camera": "LEFT"},
    "right": {"camera": "RIGHT"},
    "top": {"camera": "TOP"},
    "bottom": {"camera": "BOTTOM"},
    "iso": {"camera": "ISO"},
    "iso_front_right": {"direction": "iso_front_right"},
    "iso_rear_right": {"direction": "iso_rear_right"},
    "iso_rear_left": {"direction": "iso_rear_left"},
    "iso_front_left": {"direction": "iso_front_left"},
    "iso_top_front_right": {"direction": "iso_top_front_right"},
    # Close variants keep canonical orientations but fill more of the frame.
    "front_close": {"camera": "FRONT", "zoom": 1.8},
    "rear_close": {"camera": "BACK", "zoom": 1.8},
    "left_close": {"camera": "LEFT", "zoom": 1.8},
    "right_close": {"camera": "RIGHT", "zoom": 1.8},
    "top_close": {"camera": "TOP", "zoom": 1.65},
    "iso_close": {"camera": "ISO", "zoom": 1.45},
    # X-ray variants render with per-body alpha blending so internal features
    # (shafts, pockets, fasteners) are visible through outer walls. Most useful
    # for multi-body assemblies; on single-body parts the effect degrades to a
    # tinted silhouette.
    "iso_xray": {"camera": "ISO", "transparent": True},
    "front_xray": {"camera": "FRONT", "transparent": True},
    "right_xray": {"camera": "RIGHT", "transparent": True},
}


def _safe_token(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return cleaned.strip("._") or "render"


def make_render_set_id(prefix: str) -> str:
    return f"{_safe_token(prefix)}_{int(time.time())}_{uuid.uuid4().hex[:8]}"


def _normalize_render_set_id(prefix: str, render_set_id: str | None) -> str:
    if render_set_id is None:
        return make_render_set_id(prefix)
    cleaned = _safe_token(render_set_id)
    if cleaned != render_set_id.strip():
        raise ValueError(f"Unsafe render_set_id: {render_set_id!r}")
    return cleaned


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.contextmanager
def _exclusive_render_lock():
    _OCP_RENDER_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with _OCP_RENDER_LOCK.open("w", encoding="utf-8") as lock_file:
        if os.name == "posix":
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "posix":
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


class _StreamPump:
    def __init__(self, stream):
        self.lines: "queue.Queue[str]" = queue.Queue()
        self.seen: list[str] = []
        self._thread = threading.Thread(target=self._pump, args=(stream,), daemon=True)
        self._thread.start()

    def _pump(self, stream) -> None:
        for line in iter(stream.readline, ""):
            self.lines.put(line)
        stream.close()

    def wait_for(self, token: str, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                line = self.lines.get(timeout=0.1)
            except queue.Empty:
                continue
            self.seen.append(line.rstrip())
            if token in line:
                return True
        return False

    def drain(self) -> list[str]:
        while True:
            try:
                self.seen.append(self.lines.get_nowait().rstrip())
            except queue.Empty:
                return self.seen


def _terminate_process(proc: subprocess.Popen | None, timeout: float = 5.0) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=timeout)


def _is_nonblank_png(path: Path) -> tuple[bool, str]:
    if not path.is_file():
        return False, "missing file"
    if path.stat().st_size <= 0:
        return False, "empty file"
    try:
        from PIL import Image
    except ImportError:
        return False, "pillow is not installed"
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            rgb = image.convert("RGB")
            extrema = rgb.getextrema()
    except Exception as exc:  # noqa: BLE001
        return False, f"invalid PNG: {exc}"
    if all(low == high for low, high in extrema):
        return False, "blank single-color image"
    return True, ""


def _render_server_script(step_path: Path, port: int) -> str:
    return f"""
import sys
import socket
import threading
import time

import cadquery as cq
from ocp_vscode import Camera, show
from ocp_vscode.comms import set_port
from ocp_vscode.standalone import Viewer

step_path = {str(step_path)!r}
port = {port!r}
assy = cq.importers.importStep(step_path)
viewer = Viewer({{"port": port, "host": "127.0.0.1", "tools": False, "axes": False, "grid": False}})
threading.Thread(target=viewer.start, daemon=True).start()
set_port(port)

deadline = time.time() + 30
while time.time() < deadline:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if sock.connect_ex(("127.0.0.1", port)) == 0:
            break
    time.sleep(0.1)
else:
    print("SERVER_FAILED port_not_ready", flush=True)
    sys.exit(1)

print("SERVER_READY", flush=True)

for raw in sys.stdin:
    camera_name = raw.strip()
    if not camera_name:
        continue
    if camera_name == "__quit__":
        break
    deadline = time.time() + 30
    while viewer.javascript_client is None and time.time() < deadline:
        time.sleep(0.1)
    if viewer.javascript_client is None:
        print(f"VIEW_FAILED {{camera_name}} browser_not_connected", flush=True)
        continue
    try:
        show(
            assy,
            port=port,
            reset_camera=getattr(Camera, camera_name),
            tools=False,
            axes=False,
            grid=False,
            transparent=False,
            render_edges=True,
            progress=None,
        )
        print(f"VIEW_SENT {{camera_name}}", flush=True)
    except Exception as exc:
        print(f"VIEW_FAILED {{camera_name}} {{exc}}", flush=True)
"""


def _render_ocp_views(step_path: Path, view_paths: dict[str, Path]) -> list[str]:
    try:
        __import__("ocp_vscode")
    except ImportError:
        return ["ocp-vscode is not installed. Install ocp-vscode==3.1.2."]
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError:
        return [
            "playwright is not installed. Install playwright and run "
            "`python -m playwright install chromium` in the active environment."
        ]

    errors: list[str] = []
    port = _free_port()
    proc: subprocess.Popen | None = None
    browser = None
    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", _render_server_script(step_path, port)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        assert proc.stderr is not None
        stdout = _StreamPump(proc.stdout)
        stderr = _StreamPump(proc.stderr)
        if not stdout.wait_for("SERVER_READY", 30):
            errors.append(
                "OCP viewer server did not become ready. stdout="
                + repr(stdout.drain()[-20:])
                + " stderr="
                + repr(stderr.drain()[-20:])
            )
            return errors

        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            except PlaywrightError as exc:
                errors.append(
                    "Chromium could not be launched. Run "
                    "`python -m playwright install chromium` in the active environment. "
                    f"Details: {exc}"
                )
                return errors
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/viewer", timeout=30000)
            canvas = page.locator("canvas").first
            canvas.wait_for(state="visible", timeout=30000)

            for view_name in view_paths:
                camera_name = _OCP_CAMERA_BY_VIEW[view_name]
                assert proc.stdin is not None
                proc.stdin.write(f"{camera_name}\n")
                proc.stdin.flush()
                if not stdout.wait_for(f"VIEW_SENT {camera_name}", 30):
                    if any(f"VIEW_FAILED {camera_name}" in line for line in stdout.drain()):
                        errors.append(f"{view_name}: OCP reported VIEW_FAILED")
                    else:
                        errors.append(f"{view_name}: timed out waiting for OCP camera update")
                    continue
                page.wait_for_timeout(1200)
                canvas.screenshot(path=str(view_paths[view_name]), timeout=30000)
    except PlaywrightError as exc:
        errors.append(f"Playwright failed: {exc}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"OCP render failed: {exc}")
    finally:
        if browser is not None:
            with contextlib.suppress(Exception):
                browser.close()
        if proc is not None:
            with contextlib.suppress(Exception):
                if proc.stdin is not None and proc.poll() is None:
                    proc.stdin.write("__quit__\n")
                    proc.stdin.flush()
            _terminate_process(proc)
    return errors


def _custom_render_server_script(step_path: Path, port: int) -> str:
    return f"""
import json
import socket
import sys
import threading
import time

import cadquery as cq
from ocp_vscode import Camera, show
from ocp_vscode.comms import set_port
from ocp_vscode.standalone import Viewer

step_path = {str(step_path)!r}
port = {port!r}
assy = cq.importers.importStep(step_path)
viewer = Viewer({{"port": port, "host": "127.0.0.1", "tools": False, "axes": False, "grid": False}})
threading.Thread(target=viewer.start, daemon=True).start()
set_port(port)

deadline = time.time() + 30
while time.time() < deadline:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if sock.connect_ex(("127.0.0.1", port)) == 0:
            break
    time.sleep(0.1)
else:
    print("SERVER_FAILED port_not_ready", flush=True)
    sys.exit(1)

print("SERVER_READY", flush=True)

for raw in sys.stdin:
    payload = raw.strip()
    if not payload:
        continue
    if payload == "__quit__":
        break
    try:
        request = json.loads(payload)
    except Exception as exc:
        print(f"VIEW_FAILED invalid_request {{exc}}", flush=True)
        continue

    view_name = str(request.get("view_name") or "unnamed")
    deadline = time.time() + 30
    while viewer.javascript_client is None and time.time() < deadline:
        time.sleep(0.1)
    if viewer.javascript_client is None:
        print(f"VIEW_FAILED {{view_name}} browser_not_connected", flush=True)
        continue

    show_kwargs = {{
        "port": port,
        "tools": False,
        "axes": False,
        "grid": False,
        "transparent": False,
        "render_edges": True,
        "progress": None,
        "reset_camera": getattr(Camera, str(request.get("reset_camera") or "RESET").upper()),
    }}
    for field in ("position", "quaternion", "target", "zoom", "transparent"):
        value = request.get(field)
        if value is not None:
            show_kwargs[field] = value

    try:
        show(assy, **show_kwargs)
        print(f"VIEW_SENT {{view_name}}", flush=True)
    except Exception as exc:
        print(f"VIEW_FAILED {{view_name}} {{exc}}", flush=True)
"""


def _render_ocp_custom_views(
    step_path: Path,
    view_specs: dict[str, dict[str, object]],
    view_paths: dict[str, Path],
) -> list[str]:
    try:
        __import__("ocp_vscode")
    except ImportError:
        return ["ocp-vscode is not installed. Install ocp-vscode==3.1.2."]
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError:
        return [
            "playwright is not installed. Install playwright and run "
            "`python -m playwright install chromium` in the active environment."
        ]

    errors: list[str] = []
    port = _free_port()
    proc: subprocess.Popen | None = None
    browser = None
    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", _custom_render_server_script(step_path, port)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        assert proc.stderr is not None
        stdout = _StreamPump(proc.stdout)
        stderr = _StreamPump(proc.stderr)
        if not stdout.wait_for("SERVER_READY", 30):
            errors.append(
                "OCP viewer server did not become ready. stdout="
                + repr(stdout.drain()[-20:])
                + " stderr="
                + repr(stderr.drain()[-20:])
            )
            return errors

        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            except PlaywrightError as exc:
                errors.append(
                    "Chromium could not be launched. Run "
                    "`python -m playwright install chromium` in the active environment. "
                    f"Details: {exc}"
                )
                return errors
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/viewer", timeout=30000)
            canvas = page.locator("canvas").first
            canvas.wait_for(state="visible", timeout=30000)

            for view_name, spec in view_specs.items():
                request = {"view_name": view_name, **spec}
                assert proc.stdin is not None
                proc.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
                proc.stdin.flush()
                if not stdout.wait_for(f"VIEW_SENT {view_name}", 30):
                    failed = next(
                        (line for line in stdout.drain() if f"VIEW_FAILED {view_name}" in line),
                        None,
                    )
                    if failed is not None:
                        errors.append(f"{view_name}: {failed}")
                    else:
                        errors.append(f"{view_name}: timed out waiting for OCP camera update")
                    continue
                page.wait_for_timeout(1200)
                canvas.screenshot(path=str(view_paths[view_name]), timeout=30000)
    except PlaywrightError as exc:
        errors.append(f"Playwright failed: {exc}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"OCP render failed: {exc}")
    finally:
        if browser is not None:
            with contextlib.suppress(Exception):
                browser.close()
        if proc is not None:
            with contextlib.suppress(Exception):
                if proc.stdin is not None and proc.poll() is None:
                    proc.stdin.write("__quit__\n")
                    proc.stdin.flush()
            _terminate_process(proc)
    return errors


def _step_center_and_radius(step_path: Path) -> tuple[tuple[float, float, float], float]:
    try:
        import cadquery as cq
    except ImportError as exc:
        raise RuntimeError("cadquery is not installed in the active environment") from exc

    try:
        imported = cq.importers.importStep(str(step_path))
        shape = imported.val() if hasattr(imported, "val") else imported
        bbox = shape.BoundingBox()
    except Exception as exc:
        raise RuntimeError(f"Failed to read STEP geometry: {exc}") from exc

    center_raw = bbox.center
    if hasattr(center_raw, "toTuple"):
        center = tuple(float(value) for value in center_raw.toTuple())
    else:
        center = (float(center_raw.x), float(center_raw.y), float(center_raw.z))
    radius = max(math.sqrt(bbox.xlen**2 + bbox.ylen**2 + bbox.zlen**2) / 2.0, 1.0)
    return center, float(radius)


def _build_custom_view_specs(
    step_path: Path,
    *,
    view_names: list[str],
    distance_factor: float,
) -> dict[str, dict[str, object]]:
    if distance_factor <= 0:
        raise ValueError("distance_factor must be > 0")
    if not view_names:
        raise ValueError("view_names must include at least one custom direction")
    unknown = [name for name in view_names if name not in _CUSTOM_VIEW_VECTOR_BY_NAME]
    if unknown:
        allowed = ", ".join(sorted(_CUSTOM_VIEW_VECTOR_BY_NAME))
        raise ValueError(f"Unknown custom views: {', '.join(unknown)}. Allowed: {allowed}")
    center, radius = _step_center_and_radius(step_path)
    camera_distance = distance_factor * radius
    specs: dict[str, dict[str, object]] = {}
    for name in view_names:
        direction = _CUSTOM_VIEW_VECTOR_BY_NAME[name]
        length = math.sqrt(sum(component * component for component in direction))
        position = [
            center[idx] + (camera_distance * direction[idx] / length)
            for idx in range(3)
        ]
        specs[name] = {
            "reset_camera": "RESET",
            "position": position,
            "target": list(center),
        }
    return specs


def _build_rich_view_specs(
    step_path: Path,
    *,
    view_names: list[str],
    distance_factor: float,
) -> dict[str, dict[str, object]]:
    if distance_factor <= 0:
        raise ValueError("distance_factor must be > 0")
    if not view_names:
        raise ValueError("view_names must include at least one rich view")
    unknown = [name for name in view_names if name not in _RICH_VIEW_PRESET_BY_NAME]
    if unknown:
        allowed = ", ".join(sorted(_RICH_VIEW_PRESET_BY_NAME))
        raise ValueError(f"Unknown rich views: {', '.join(unknown)}. Allowed: {allowed}")

    needs_geometry = any("direction" in _RICH_VIEW_PRESET_BY_NAME[name] for name in view_names)
    center: tuple[float, float, float] | None = None
    radius = 1.0
    if needs_geometry:
        center, radius = _step_center_and_radius(step_path)

    specs: dict[str, dict[str, object]] = {}
    for name in view_names:
        preset = _RICH_VIEW_PRESET_BY_NAME[name]
        spec: dict[str, object] = {"reset_camera": str(preset.get("camera") or "RESET")}
        if preset.get("zoom") is not None:
            spec["zoom"] = float(preset["zoom"])
        if preset.get("transparent"):
            spec["transparent"] = True

        direction_name = preset.get("direction")
        if direction_name is not None:
            assert center is not None
            direction = _CUSTOM_VIEW_VECTOR_BY_NAME[str(direction_name)]
            length = math.sqrt(sum(component * component for component in direction))
            camera_distance = float(preset.get("distance_factor") or distance_factor) * radius
            spec["reset_camera"] = "RESET"
            spec["position"] = [
                center[idx] + (camera_distance * direction[idx] / length)
                for idx in range(3)
            ]
            spec["target"] = list(center)

        specs[name] = spec
    return specs


def _build_iso_orbit_view_specs(
    step_path: Path,
    *,
    directions_csv: str,
    distance_factor: float,
) -> dict[str, dict[str, object]]:
    requested = [token.strip() for token in directions_csv.split(",") if token.strip()]
    if not requested:
        raise ValueError("directions_csv must include at least one direction")
    unknown = [name for name in requested if name not in _ISO_ORBIT_VIEW_NAMES]
    if unknown:
        allowed = ", ".join(_ISO_ORBIT_VIEW_NAMES)
        raise ValueError(f"Unknown iso directions: {', '.join(unknown)}. Allowed: {allowed}")
    return _build_custom_view_specs(
        step_path,
        view_names=requested,
        distance_factor=distance_factor,
    )


def _render_result_yaml(
    *,
    status: str,
    render_set_id: str,
    render_set_dir: Path,
    manifest_path: Path,
    views: dict[str, Path],
    errors: list[str],
) -> str:
    return yaml.safe_dump(
        {
            "status": status,
            "render_set_id": render_set_id,
            "render_set_dir": str(render_set_dir),
            "manifest_path": str(manifest_path),
            "total_rendered": sum(1 for path in views.values() if path.is_file()),
            "total_errors": len(errors),
            "views": {name: str(path) for name, path in views.items()},
            "errors": errors,
        },
        sort_keys=False,
    )


def _render_doc(
    *,
    step_path: str,
    output_dir: str,
    prefix: str,
    view_mode: str,
    target_type: str = "",
    target_id: str = "",
    attempt: int = 0,
    distance_factor: float = 5.0,
    render_set_id: str | None = None,
    runtime: ToolRuntime | None = None,
) -> dict[str, object]:
    view_order = list(_VIEW_MODE_TO_ORDER[view_mode])
    step = _resolve_pipeline_tool_path(step_path, runtime)
    out = _resolve_pipeline_tool_path(output_dir, runtime)
    render_set_id = _normalize_render_set_id(prefix, render_set_id)
    render_set_dir = out / "render_sets" / render_set_id
    render_set_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = render_set_dir / "manifest.yaml"
    views = {name: render_set_dir / f"view_{name}.png" for name in view_order}

    errors: list[str] = []
    if not step.is_file():
        errors.append(f"STEP file not found: {step}")
    else:
        with _exclusive_render_lock():
            if view_mode == "richview":
                try:
                    view_specs = _build_rich_view_specs(
                        step,
                        view_names=view_order,
                        distance_factor=distance_factor,
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append(str(exc))
                else:
                    errors.extend(_render_ocp_custom_views(step, view_specs, views))
            else:
                standard_views = {
                    name: path for name, path in views.items() if name in _OCP_CAMERA_BY_VIEW
                }
                custom_view_names = [name for name in view_order if name in _CUSTOM_VIEW_VECTOR_BY_NAME]
                custom_views = {name: views[name] for name in custom_view_names}
                if standard_views:
                    errors.extend(_render_ocp_views(step, standard_views))
                if custom_views:
                    try:
                        view_specs = _build_custom_view_specs(
                            step,
                            view_names=custom_view_names,
                            distance_factor=distance_factor,
                        )
                    except Exception as exc:  # noqa: BLE001
                        errors.append(str(exc))
                    else:
                        errors.extend(_render_ocp_custom_views(step, view_specs, custom_views))

    for view_name, view_path in views.items():
        ok, reason = _is_nonblank_png(view_path)
        if not ok:
            errors.append(f"{view_name}: {reason}")

    status = "pass" if not errors else "fail"
    doc = {
        "status": status,
        "view_mode": view_mode,
        "view_order": view_order,
        "render_set_id": render_set_id,
        "render_set_dir": str(render_set_dir),
        "manifest_path": str(manifest_path),
        "target_type": target_type or "unknown",
        "target_id": target_id or prefix,
        "attempt": attempt,
        "step_path": str(step),
        "total_rendered": sum(1 for path in views.values() if path.is_file()),
        "total_errors": len(errors),
        "views": {name: str(path) for name, path in views.items()},
        "errors": errors,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    manifest_path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return doc


def render_7view_result(
    step_path: str,
    output_dir: str,
    prefix: str,
    target_type: str = "",
    target_id: str = "",
    attempt: int = 0,
    render_set_id: str | None = None,
    runtime: ToolRuntime | None = None,
) -> dict[str, object]:
    return _render_doc(
        step_path=step_path,
        output_dir=output_dir,
        prefix=prefix,
        view_mode="7view",
        target_type=target_type,
        target_id=target_id,
        attempt=attempt,
        render_set_id=render_set_id,
        runtime=runtime,
    )


def render_12view_result(
    step_path: str,
    output_dir: str,
    prefix: str,
    target_type: str = "",
    target_id: str = "",
    attempt: int = 0,
    render_set_id: str | None = None,
    runtime: ToolRuntime | None = None,
) -> dict[str, object]:
    return _render_doc(
        step_path=step_path,
        output_dir=output_dir,
        prefix=prefix,
        view_mode="12view",
        target_type=target_type,
        target_id=target_id,
        attempt=attempt,
        render_set_id=render_set_id,
        runtime=runtime,
    )


def render_richview_result(
    step_path: str,
    output_dir: str,
    prefix: str,
    target_type: str = "",
    target_id: str = "",
    attempt: int = 0,
    render_set_id: str | None = None,
    runtime: ToolRuntime | None = None,
) -> dict[str, object]:
    return _render_doc(
        step_path=step_path,
        output_dir=output_dir,
        prefix=prefix,
        view_mode="richview",
        target_type=target_type,
        target_id=target_id,
        attempt=attempt,
        distance_factor=3.5,
        render_set_id=render_set_id,
        runtime=runtime,
    )


def render_custom_views_result(
    step_path: str,
    output_dir: str,
    prefix: str,
    *,
    view_order: list[str],
    view_specs: dict[str, dict[str, object]],
    target_type: str = "",
    target_id: str = "",
    attempt: int = 0,
    runtime: ToolRuntime | None = None,
) -> dict[str, object]:
    step = _resolve_pipeline_tool_path(step_path, runtime)
    out = _resolve_pipeline_tool_path(output_dir, runtime)
    render_set_id = f"{_safe_token(prefix)}_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    render_set_dir = out / "render_sets" / render_set_id
    render_set_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = render_set_dir / "manifest.yaml"
    views = {name: render_set_dir / f"view_{name}.png" for name in view_order}

    errors: list[str] = []
    if not step.is_file():
        errors.append(f"STEP file not found: {step}")
    elif set(view_specs) != set(view_order):
        errors.append("Custom render view_specs must match view_order exactly")
    else:
        ordered_specs = {name: view_specs[name] for name in view_order}
        with _exclusive_render_lock():
            errors.extend(_render_ocp_custom_views(step, ordered_specs, views))

    for view_name, view_path in views.items():
        ok, reason = _is_nonblank_png(view_path)
        if not ok:
            errors.append(f"{view_name}: {reason}")

    status = "pass" if not errors else "fail"
    doc = {
        "status": status,
        "view_mode": "custom",
        "view_order": view_order,
        "render_set_id": render_set_id,
        "render_set_dir": str(render_set_dir),
        "manifest_path": str(manifest_path),
        "target_type": target_type or "unknown",
        "target_id": target_id or prefix,
        "attempt": attempt,
        "step_path": str(step),
        "total_rendered": sum(1 for path in views.values() if path.is_file()),
        "total_errors": len(errors),
        "views": {name: str(path) for name, path in views.items()},
        "errors": errors,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    manifest_path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return doc


def _image_block_for_path(image_path: Path) -> dict[str, object]:
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{base64.b64encode(image_path.read_bytes()).decode('ascii')}"},
    }


def _render_tool_blocks(render_doc: dict[str, object]) -> list[dict[str, object]]:
    blocks: list[dict[str, object]] = [
        {"type": "text", "text": yaml.safe_dump(render_doc, sort_keys=False)}
    ]
    if render_doc.get("status") != "pass":
        return blocks
    views = render_doc.get("views") or {}
    for view_name in render_doc.get("view_order") or []:
        image_path = Path(str(views[view_name]))
        ok, _reason = _is_nonblank_png(image_path)
        if ok:
            blocks.append(_image_block_for_path(image_path))
    return blocks


def _coerce_expected_contact_specs(spec: str) -> list[str]:
    return [token.strip() for token in spec.split(",") if token.strip()]


def check_assembly_interference_result(
    step_path: str,
    assembly_script_path: str = "",
    plan_path: str = "",
    expected_contact_specs: str = "",
    overlap_tol_mm3: float = 1e-3,
    clearance_warn_mm: float = 0.1,
    runtime: ToolRuntime | None = None,
) -> dict[str, object]:
    step = _resolve_pipeline_tool_path(step_path, runtime)
    report_path = step.with_name(f"{step.stem}_interference_report.yaml")
    script_path = (
        _resolve_pipeline_tool_path(assembly_script_path, runtime)
        if assembly_script_path
        else None
    )
    plan = _resolve_pipeline_tool_path(plan_path, runtime) if plan_path else None
    try:
        from tools.experimental_interference import analyze_step_with_script

        report = analyze_step_with_script(
            step,
            assembly_script_path=script_path,
            plan_path=plan,
            expected_contact_specs=_coerce_expected_contact_specs(expected_contact_specs),
            overlap_tol_mm3=overlap_tol_mm3,
            clearance_warn_mm=clearance_warn_mm,
        )
        report["errors"] = []
    except Exception as exc:  # noqa: BLE001
        report = {
            "status": "fail",
            "step_path": str(step),
            "assembly_script_path": str(script_path) if script_path else "",
            "part_count": 0,
            "pair_count": 0,
            "overlap_tolerance_mm3": overlap_tol_mm3,
            "clearance_warning_mm": clearance_warn_mm,
            "counts": {
                "fail_interference": 0,
                "warn_unexpected_contact": 0,
                "warn_expected_contact_gap": 0,
                "pass_expected_contact": 0,
                "pass_clear": 0,
            },
            "parts": [],
            "pairs": [],
            "expected_contacts": [],
            "expected_contact_sources": {"plan_inferred": [], "explicit": []},
            "script_stdout": [],
            "errors": [str(exc)],
        }
    report["report_path"] = str(report_path)
    report_path.write_text(yaml.safe_dump(report, sort_keys=False), encoding="utf-8")
    return report


def check_active_mate_fit_result(
    step_path: str,
    *,
    active_mates: list[dict[str, object]],
    thresholds_by_name: dict[str, dict[str, float]],
    assembly_script_path: str = "",
    runtime: ToolRuntime | None = None,
) -> dict[str, object]:
    step = _resolve_pipeline_tool_path(step_path, runtime)
    report_path = step.with_name(f"{step.stem}_active_mate_report.yaml")
    script_path = (
        _resolve_pipeline_tool_path(assembly_script_path, runtime)
        if assembly_script_path
        else None
    )
    try:
        from tools.experimental_interference import analyze_active_mates_with_script

        report = analyze_active_mates_with_script(
            step,
            active_mates=active_mates,
            thresholds_by_name=thresholds_by_name,
            assembly_script_path=script_path,
        )
        report["errors"] = []
    except Exception as exc:  # noqa: BLE001
        report = {
            "status": "fail",
            "step_path": str(step),
            "assembly_script_path": str(script_path) if script_path else "",
            "active_mates": [],
            "errors": [str(exc)],
        }
    report["report_path"] = str(report_path)
    report_path.write_text(yaml.safe_dump(report, sort_keys=False), encoding="utf-8")
    return report


@tool(parse_docstring=True)
def render_7view(
    step_path: str,
    output_dir: str,
    prefix: str,
    target_type: str = "",
    target_id: str = "",
    attempt: int = 0,
    runtime: Annotated[ToolRuntime | None, InjectedToolArg] = None,
) -> list[dict[str, object]]:
    """Render 7 standard views of a STEP file to PNG images with OCP/Playwright.

    Creates a per-attempt render set under ``output_dir/render_sets`` and writes
    a manifest.yaml alongside view_front.png, view_rear.png, view_left.png,
    view_right.png, view_top.png, view_bottom.png, and view_iso.png.

    CAD Gen uses controller-owned prefixes such as:
    - ``cg1_{part_name}_7view_r{revision}_a{attempt}``
    - ``ca1_step_{step_index}_7view_a{attempt}``

    Args:
        step_path: Absolute path to the STEP file to render.
        output_dir: Directory where render_sets will be written.
        prefix: Stable filename-safe prefix for this render attempt.
        target_type: Optional target type, usually "part" or "assembly".
        target_id: Optional target identifier, such as a part name or assembly step.
        attempt: Optional attempt number for the rendered target.

    Returns:
        Multimodal content blocks with a YAML summary text block first and,
        on success, ordered ``image_url`` blocks matching the 7-view order.
    """
    return _render_tool_blocks(
        render_7view_result(
            step_path=step_path,
            output_dir=output_dir,
            prefix=prefix,
            target_type=target_type,
            target_id=target_id,
            attempt=attempt,
            runtime=runtime,
        )
    )


@tool(parse_docstring=True)
def render_12view(
    step_path: str,
    output_dir: str,
    prefix: str,
    target_type: str = "",
    target_id: str = "",
    attempt: int = 0,
    runtime: Annotated[ToolRuntime | None, InjectedToolArg] = None,
) -> list[dict[str, object]]:
    """Render 12 controller-selected views of a STEP file to PNG images.

    Creates a per-attempt render set under ``output_dir/render_sets`` and writes
    a manifest.yaml plus 12 PNG files covering the standard orthographic/iso
    views and five additional isometric directions.

    Args:
        step_path: Absolute path to the STEP file to render.
        output_dir: Directory where render_sets will be written.
        prefix: Stable filename-safe prefix for this render attempt.
        target_type: Optional target type, usually "part" or "assembly".
        target_id: Optional target identifier, such as a part name or assembly step.
        attempt: Optional attempt number for the rendered target.

    Returns:
        Multimodal content blocks with a YAML summary text block first and,
        on success, ordered ``image_url`` blocks matching the 12-view order.
    """
    return _render_tool_blocks(
        render_12view_result(
            step_path=step_path,
            output_dir=output_dir,
            prefix=prefix,
            target_type=target_type,
            target_id=target_id,
            attempt=attempt,
            runtime=runtime,
        )
    )


@tool(parse_docstring=True)
def render_richview(
    step_path: str,
    output_dir: str,
    prefix: str,
    target_type: str = "",
    target_id: str = "",
    attempt: int = 0,
    runtime: Annotated[ToolRuntime | None, InjectedToolArg] = None,
) -> list[dict[str, object]]:
    """Render 21 controller-selected views of a STEP file to PNG images.

    Creates a per-attempt render set under ``output_dir/render_sets`` and writes
    a manifest.yaml plus 21 PNG files. The ordered set keeps the full 12-view
    contract, adds six closer canonical views so judges get more frame-filling
    geometry without giving up orthographic references, and appends three x-ray
    frames (``iso_xray``, ``front_xray``, ``right_xray``) rendered with per-body
    alpha blending so internal features are visible through outer walls. X-ray
    is most informative on multi-body assemblies; on single-body parts it
    degrades to a tinted silhouette.

    Args:
        step_path: Absolute path to the STEP file to render.
        output_dir: Directory where render_sets will be written.
        prefix: Stable filename-safe prefix for this render attempt.
        target_type: Optional target type, usually "part" or "assembly".
        target_id: Optional target identifier, such as a part name or assembly step.
        attempt: Optional attempt number for the rendered target.

    Returns:
        Multimodal content blocks with a YAML summary text block first and,
        on success, ordered ``image_url`` blocks matching the rich-view order.
    """
    return _render_tool_blocks(
        render_richview_result(
            step_path=step_path,
            output_dir=output_dir,
            prefix=prefix,
            target_type=target_type,
            target_id=target_id,
            attempt=attempt,
            runtime=runtime,
        )
    )


@tool(parse_docstring=True)
def check_assembly_interference(
    step_path: str,
    assembly_script_path: str = "",
    plan_path: str = "",
    expected_contact_specs: str = "",
    overlap_tol_mm3: float = 1e-3,
    clearance_warn_mm: float = 0.1,
    runtime: Annotated[ToolRuntime | None, InjectedToolArg] = None,
) -> str:
    """Analyze an assembly STEP for geometric overlap and tight-clearance issues.

    The checker runs the adjacent ``ca1_*_assembly.py`` script to reconstruct
    named placed solids, then evaluates pairwise overlap volume and minimum
    distance. True overlap is a hard failure. Tight clearances and expected
    contact gaps are warnings.

    Args:
        step_path: Absolute path to the assembly STEP file.
        assembly_script_path: Optional assembly script path. Defaults to the STEP path with .py suffix.
        plan_path: Optional cad_gen_plan.yaml path for inferred expected contacts.
        expected_contact_specs: Optional comma-separated explicit contact pairs, e.g. ``part_a:part_b,part_c:part_d``.
        overlap_tol_mm3: Overlap volume above this threshold counts as interference.
        clearance_warn_mm: Distance at or below this threshold counts as contact/tight clearance.

    Returns:
        YAML text with overall status, counts, pairwise measurements, report path,
        and any runtime or assembly-object errors.
    """
    return yaml.safe_dump(
        check_assembly_interference_result(
            step_path=step_path,
            assembly_script_path=assembly_script_path,
            plan_path=plan_path,
            expected_contact_specs=expected_contact_specs,
            overlap_tol_mm3=overlap_tol_mm3,
            clearance_warn_mm=clearance_warn_mm,
            runtime=runtime,
        ),
        sort_keys=False,
    )


@tool(parse_docstring=True)
def render_iso_orbit(
    step_path: str,
    output_dir: str,
    prefix: str,
    directions_csv: str = ",".join(_ISO_ORBIT_VIEW_NAMES),
    target_type: str = "",
    target_id: str = "",
    attempt: int = 0,
    distance_factor: float = 5.0,
    runtime: Annotated[ToolRuntime | None, InjectedToolArg] = None,
) -> str:
    """Render multiple isometric orbit views of a STEP file to PNG images.

    This is an experimental renderer for comparing iso-style angles without
    modifying the production 7-view controller contract.

    Creates a per-attempt render set under ``output_dir/render_sets`` and
    writes a manifest.yaml alongside one PNG per requested iso direction.

    Supported directions are:
    - ``iso_front_right``
    - ``iso_rear_right``
    - ``iso_rear_left``
    - ``iso_front_left``

    Args:
        step_path: Absolute path to the STEP file to render.
        output_dir: Directory where render_sets will be written.
        prefix: Stable filename-safe prefix for this render attempt.
        directions_csv: Comma-separated subset of supported iso directions.
        target_type: Optional target type, usually "part" or "assembly".
        target_id: Optional target identifier, such as a part name or assembly step.
        attempt: Optional attempt number for the rendered target.
        distance_factor: Multiplier applied to the STEP bounding radius.

    Returns:
        YAML text with status, manifest_path, render_set_dir, total_rendered,
        total_errors, view paths, and errors.
    """
    step = _resolve_pipeline_tool_path(step_path, runtime)
    out = _resolve_pipeline_tool_path(output_dir, runtime)
    render_set_id = f"{_safe_token(prefix)}_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    render_set_dir = out / "render_sets" / render_set_id
    render_set_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = render_set_dir / "manifest.yaml"

    errors: list[str] = []
    view_specs: dict[str, dict[str, object]] = {}
    if not step.is_file():
        errors.append(f"STEP file not found: {step}")
    else:
        try:
            view_specs = _build_iso_orbit_view_specs(
                step,
                directions_csv=directions_csv,
                distance_factor=distance_factor,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))

    requested_view_names = list(view_specs) if view_specs else [
        token.strip() for token in directions_csv.split(",") if token.strip()
    ]
    views = {
        name: render_set_dir / f"view_{name}.png"
        for name in requested_view_names
    }

    if step.is_file() and view_specs:
        with _exclusive_render_lock():
            errors.extend(_render_ocp_custom_views(step, view_specs, views))

    for view_name, view_path in views.items():
        ok, reason = _is_nonblank_png(view_path)
        if not ok:
            errors.append(f"{view_name}: {reason}")

    status = "pass" if not errors else "fail"
    manifest = {
        "render_set_id": render_set_id,
        "target_type": target_type or "unknown",
        "target_id": target_id or prefix,
        "attempt": attempt,
        "step_path": str(step),
        "status": status,
        "view_mode": "iso_orbit",
        "directions": requested_view_names,
        "view_specs": view_specs,
        "views": {name: str(path) for name, path in views.items()},
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "errors": errors,
    }
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

    return _render_result_yaml(
        status=status,
        render_set_id=render_set_id,
        render_set_dir=render_set_dir,
        manifest_path=manifest_path,
        views=views,
        errors=errors,
    )
