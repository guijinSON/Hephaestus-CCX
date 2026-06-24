#!/usr/bin/env python3
"""End-to-end CCX eval runner for one item.

Usage:
    grade_ccx.py <workdir>

Workdir must contain BEFORE the run:
    build.py                  agent submission (cadquery script that emits out.step + meta.json)
    analysis_template.inp     spec-side ccx deck (materials, BCs by NSET name, loads, *STEPs)
    check.py                  spec-side post-processor (parses model.dat, prints PASS/FAIL/SKIP)

The runner produces (and overwrites if present):
    out.step + meta.json      from `python build.py`
    mesh.inp                  from gmsh -3 out.step
    model.inp                 from wire_bcs.py (mesh.inp + meta.json + template)
    model.dat / model.frd     from ccx_2.22 model
    check.log                 from `python check.py`
    grade.json                run summary {build_rc, gmsh_rc, wire_rc, ccx_rc, check_rc, ...}

Exit code: 0 if every stage rc==0, else first non-zero stage rc.
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WIRE = Path(__file__).resolve().parent / "wire_bcs.py"
CCX = os.environ.get("CCX", "/opt/homebrew/bin/ccx_2.22")
PY = os.environ.get("EVAL_PYTHON", "/opt/anaconda3/envs/cadquery/bin/python")
GMSH = os.environ.get("GMSH", "")  # if empty, use python -m gmsh


def run(cmd: list[str], cwd: Path, log: Path) -> int:
    """Run a command, append stdout+stderr to log, return rc."""
    with log.open("ab") as f:
        f.write(f"\n$ {' '.join(cmd)}\n".encode())
        f.flush()
        rc = subprocess.run(cmd, cwd=cwd, stdout=f, stderr=subprocess.STDOUT).returncode
        f.write(f"\n[rc={rc}]\n".encode())
    return rc


def gmsh_step_to_inp(work: Path, step: Path, out_inp: Path, log: Path) -> int:
    """Mesh out.step into mesh.inp.

    Strategy: use gmsh's Python API since the CLI binary may not be on PATH.
    Falls back to GMSH binary if that env var is set.

    Multi-body support: if meta.json provides "body_elsets" (list of ELSET
    names in STEP body order), each imported volume is tagged as its own
    physical group with the corresponding name. SaveGroupsOfElements=1
    then writes one *ELSET per name in the .inp, in addition to the
    per-volume Volume1/Volume2 ELSETs that gmsh emits by default. This
    is how multi-material specs (e.g. COPV liner + overwrap) get
    separate ELINER / EWRAP ELSETs that the analysis_template.inp
    references in its *SOLID SECTION cards.

    Fallback (single-material): every volume is rolled into one
    "VOLUME" physical group, preserving the legacy single-material kit
    behaviour where wire_bcs.py rolls VOLUME into Eall.
    """
    if GMSH:
        return run([GMSH, "-3", str(step), "-format", "inp", "-o", str(out_inp)],
                   cwd=work, log=log)

    # Read optional body_elsets from meta.json (added for multi-material).
    body_elsets: list[str] = []
    meta_path = work / "meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            be = meta.get("body_elsets")
            if isinstance(be, list) and all(isinstance(x, str) for x in be):
                body_elsets = be
        except (json.JSONDecodeError, OSError):
            body_elsets = []

    py_script = work / "_gmsh_run.py"
    py_script.write_text(f"""\
import gmsh, sys
gmsh.initialize()
gmsh.option.setNumber("General.Terminal", 1)
gmsh.option.setNumber("Mesh.Algorithm3D", 10)   # HXT for tets
gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 12)
gmsh.option.setNumber("Mesh.MeshSizeMin", 1.0)
gmsh.option.setNumber("Mesh.MeshSizeMax", 50.0)
# Only export elements in physical groups (skips edge T3D2 / face CPS3 noise that
# breaks *SOLID SECTION).
gmsh.option.setNumber("Mesh.SaveAll", 0)
# Emit named *ELSETs from physical groups (one per group).
gmsh.option.setNumber("Mesh.SaveGroupsOfElements", 1)
gmsh.open({str(step)!r})
gmsh.model.occ.synchronize()
vols = [tag for (dim, tag) in gmsh.model.getEntities(3)]
if not vols:
    sys.exit("ERROR: STEP file contains no 3D volumes")

body_elsets = {body_elsets!r}
if body_elsets and len(body_elsets) == len(vols):
    # Multi-material conformal meshing: fragment the volumes so
    # coincident faces (e.g. a liner-wrap bonded interface) become
    # shared topology with shared mesh nodes. fragment returns a list
    # of tag-pairs in the SAME ORDER as the input vols, so we can map
    # each result volume back to its body name.
    in_dimtags = [(3, v) for v in vols]
    out_dimtags, out_map = gmsh.model.occ.fragment(in_dimtags, [])
    gmsh.model.occ.synchronize()
    # out_map[i] is the list of dim-tags that resulted from input i.
    # For two non-overlapping volumes that share a face, each input
    # maps to exactly one output volume (the face is now shared).
    # Group volumes by ELSET name first so that multiple bodies sharing
    # a name (e.g. four corner rails all tagged "RAIL") end up in one
    # physical group / one *ELSET in the meshed .inp.
    name_to_vols = {{}}
    for i, name in enumerate(body_elsets):
        new_vols = [tag for (dim, tag) in out_map[i] if dim == 3]
        if not new_vols:
            sys.exit(f"ERROR: fragment lost body '{{name}}' (input volume {{vols[i]}})")
        name_to_vols.setdefault(name, []).extend(new_vols)
    for name, group_vols in name_to_vols.items():
        gmsh.model.addPhysicalGroup(3, group_vols, name=name)
else:
    # Single-material legacy: one group "VOLUME" for all volumes.
    gmsh.model.addPhysicalGroup(3, vols, name="VOLUME")

gmsh.model.mesh.generate(3)
gmsh.write({str(out_inp)!r})
gmsh.finalize()
""")
    return run([PY, str(py_script)], cwd=work, log=log)


def main(workdir: str) -> int:
    work = Path(workdir).resolve()
    if not work.is_dir():
        print(f"ERROR: workdir not found: {work}", file=sys.stderr)
        return 64

    log = work / "grade.log"
    log.write_text(f"# grade_ccx run @ {time.strftime('%Y-%m-%d %H:%M:%S')}\n# workdir: {work}\n")

    # Required eval-side files
    for f in ("analysis_template.inp", "check.py"):
        if not (work / f).exists():
            print(f"ERROR: {f} missing in workdir", file=sys.stderr)
            return 65

    # Required submission file
    build = work / "build.py"
    if not build.exists():
        print("ERROR: build.py missing (agent submission)", file=sys.stderr)
        return 66

    summary: dict = {"workdir": str(work), "stages": {}}

    # 1. Run agent's build.py
    t = time.time()
    rc = run([PY, "build.py"], cwd=work, log=log)
    summary["stages"]["build"] = {"rc": rc, "elapsed_s": round(time.time() - t, 2)}
    if rc != 0:
        return _finalize(summary, work, rc, "build.py failed")
    for needed in ("out.step", "meta.json"):
        if not (work / needed).exists():
            return _finalize(summary, work, 67, f"build.py did not produce {needed}")

    # 2. Mesh out.step → mesh.inp
    t = time.time()
    rc = gmsh_step_to_inp(work, work / "out.step", work / "mesh.inp", log)
    summary["stages"]["gmsh"] = {"rc": rc, "elapsed_s": round(time.time() - t, 2)}
    if rc != 0:
        return _finalize(summary, work, rc, "gmsh meshing failed")

    # 3. Wire BCs
    t = time.time()
    rc = run([PY, str(WIRE), "mesh.inp", "meta.json",
              "analysis_template.inp", "model.inp"], cwd=work, log=log)
    summary["stages"]["wire_bcs"] = {"rc": rc, "elapsed_s": round(time.time() - t, 2)}
    if rc != 0:
        return _finalize(summary, work, rc, "wire_bcs.py failed")

    # 4. ccx
    t = time.time()
    rc = run([CCX, "model"], cwd=work, log=log)
    summary["stages"]["ccx"] = {"rc": rc, "elapsed_s": round(time.time() - t, 2)}
    if rc != 0:
        return _finalize(summary, work, rc, "ccx failed")

    # 5. check.py
    t = time.time()
    check_log = work / "check.log"
    check_env = os.environ.copy()
    existing_pythonpath = check_env.get("PYTHONPATH")
    check_env["PYTHONPATH"] = (
        str(REPO)
        if not existing_pythonpath
        else str(REPO) + os.pathsep + existing_pythonpath
    )
    with check_log.open("w") as f:
        rc = subprocess.run([PY, "check.py"], cwd=work,
                            stdout=f, stderr=subprocess.STDOUT,
                            env=check_env).returncode
    summary["stages"]["check"] = {"rc": rc, "elapsed_s": round(time.time() - t, 2)}
    return _finalize(summary, work, rc, "check.py rc=" + str(rc))


def _finalize(summary: dict, work: Path, rc: int, msg: str) -> int:
    summary["final_rc"] = rc
    summary["final_msg"] = msg
    (work / "grade.json").write_text(json.dumps(summary, indent=2))
    if (work / "check.log").exists():
        print("--- check.log ---")
        print((work / "check.log").read_text())
    print(f"=== final rc={rc}: {msg}")
    return rc


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        sys.exit(64)
    sys.exit(main(sys.argv[1]))
