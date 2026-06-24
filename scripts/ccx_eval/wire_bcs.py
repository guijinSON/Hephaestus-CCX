#!/usr/bin/env python3
"""Splice a gmsh-meshed .inp + meta.json + analysis_template.inp into model.inp.

Usage:
    wire_bcs.py mesh.inp meta.json analysis_template.inp model.inp

The agent's build.py emits out.step + meta.json. The runner meshes with gmsh
into mesh.inp (just *NODE + *ELEMENT). This script reads meta.json's selectors,
computes node lists from the meshed geometry, writes *NSET blocks, optionally
substitutes the agent-chosen material name into the template, and concatenates
mesh + nsets + template into the final CalculiX deck.

Selector vocabulary (in meta.json under "selectors"):
    {"face": "z_min" | "z_max" | "x_min" | "x_max" | "y_min" | "y_max",
     "tol_mm": 0.01}                          # nodes within tol of face extreme
    {"face_eq": "z" | "x" | "y", "value": 8.0,
     "tol_mm": 0.01}                          # nodes with axis = value
    {"box": [xmin,ymin,zmin, xmax,ymax,zmax]} # nodes inside AABB
    {"sphere": [cx,cy,cz, r]}                 # nodes inside sphere
    {"radius_xy": 1000.0, "tol_mm": 0.5,
     "z_range": [0.0, 1600.0]}                # nodes within tol of cylindrical
                                              #   radius sqrt(x^2+y^2)=R about Z;
                                              #   optional z_range clips axially.
                                              #   Use "axis": "x"|"y"|"z" to pick
                                              #   the cylinder axis (default z).
    {"sphere_shell": [cx,cy,cz, r], "tol_mm": 0.5,
     "axial_min_x": 250.0}                    # nodes within tol of a spherical
                                              #   shell of radius r centred at
                                              #   (cx,cy,cz). Optional
                                              #   axial_{min,max}_{x|y|z} clips
                                              #   to one hemisphere along that
                                              #   axis (e.g. axial_min_x=250
                                              #   keeps only nodes with x>=250
                                              #   for a forward hemi cap).
    {"any_of": [<sel>, <sel>, ...]}           # union (logical OR) of selectors
    {"all": true}                             # every node

Pressure-surface vocabulary (in meta.json under "pressure_surfaces"):
    Each entry maps a *SURFACE name (must match a *DSLOAD/*DLOAD reference in
    analysis_template.inp) to a node-selector dict (same grammar as above).
    wire_bcs.py finds every C3D{4,10} tetrahedral face whose three corner
    nodes all match the selector, and emits a *SURFACE, NAME=<name>,
    TYPE=ELEMENT block listing those (element_id, S{1..4}) pairs. CCX needs
    this element-face form (TYPE=NODE surfaces are only valid for cyclic
    symmetry / contact, not for pressure on solid elements).
"""
from __future__ import annotations
import json
import sys
from pathlib import Path


def parse_mesh_nodes(mesh_inp: Path) -> dict[int, tuple[float, float, float]]:
    """Return {nid: (x,y,z)} from the *NODE block(s) of an inp file."""
    nodes: dict[int, tuple[float, float, float]] = {}
    in_node = False
    for line in mesh_inp.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("**"):
            continue
        head = s.split(",")[0].strip().upper()
        if head == "*NODE":
            in_node = True
            continue
        if s.startswith("*"):
            in_node = False
            continue
        if in_node:
            parts = [p.strip() for p in s.split(",")]
            try:
                nid = int(parts[0])
                x = float(parts[1])
                y = float(parts[2]) if len(parts) > 2 else 0.0
                z = float(parts[3]) if len(parts) > 3 else 0.0
                nodes[nid] = (x, y, z)
            except (ValueError, IndexError):
                continue
    return nodes


def find_volume_elsets(mesh_inp: Path) -> list[str]:
    """Scan *ELEMENT cards for 3D types (C3D*), return their ELSET names.

    gmsh's CCX exporter emits one ELSET per geometric entity (Volume1, Volume2, …
    plus Surface1.. and Line1..). For a 3D static analysis we want only the
    volume sets — the line/surface T3D2/CPS3 elements are noise.
    """
    names: list[str] = []
    for line in mesh_inp.read_text().splitlines():
        s = line.strip()
        if not s.upper().startswith("*ELEMENT"):
            continue
        kv = {p.split("=", 1)[0].strip().upper(): p.split("=", 1)[1].strip()
              for p in s.split(",")[1:] if "=" in p}
        etype = kv.get("TYPE", "").upper()
        elset = kv.get("ELSET", "")
        if etype.startswith("C3D") and elset and elset not in names:
            names.append(elset)
    return names


def build_eall_block(volume_elsets: list[str]) -> str:
    """Emit *ELSET, ELSET=Eall combining all volume ELSETs (8 names per line)."""
    if not volume_elsets:
        return ""
    out = ["*ELSET, ELSET=Eall"]
    for i in range(0, len(volume_elsets), 8):
        out.append(",".join(volume_elsets[i:i + 8]))
    return "\n".join(out) + "\n"


def select_nodes(nodes: dict, sel: dict, default_tol: float = 1e-3) -> list[int]:
    """Apply a selector dict and return matching node IDs."""
    tol = float(sel.get("tol_mm", default_tol))

    if "face" in sel:
        face = sel["face"]
        axis_map = {
            "x_min": (0, "min"), "x_max": (0, "max"),
            "y_min": (1, "min"), "y_max": (1, "max"),
            "z_min": (2, "min"), "z_max": (2, "max"),
        }
        if face not in axis_map:
            raise ValueError(f"unknown 'face' selector: {face}")
        ax, side = axis_map[face]
        vals = [n[ax] for n in nodes.values()]
        target = min(vals) if side == "min" else max(vals)
        return [nid for nid, n in nodes.items() if abs(n[ax] - target) < tol]

    if "face_eq" in sel:
        ax = {"x": 0, "y": 1, "z": 2}[sel["face_eq"]]
        target = float(sel["value"])
        return [nid for nid, n in nodes.items() if abs(n[ax] - target) < tol]

    if "box" in sel:
        x0, y0, z0, x1, y1, z1 = [float(v) for v in sel["box"]]
        return [nid for nid, n in nodes.items()
                if x0 - tol <= n[0] <= x1 + tol
                and y0 - tol <= n[1] <= y1 + tol
                and z0 - tol <= n[2] <= z1 + tol]

    if "sphere" in sel:
        cx, cy, cz, r = [float(v) for v in sel["sphere"]]
        r2 = r * r
        return [nid for nid, n in nodes.items()
                if (n[0] - cx) ** 2 + (n[1] - cy) ** 2 + (n[2] - cz) ** 2 <= r2]

    if "radius_xy" in sel:
        # Nodes lying on a cylindrical shell at radius R (within tol_mm) about
        # the chosen axis. Optional z_range clips axially along that axis.
        # Default axis = "z"; pick "axis": "x" or "y" for other orientations.
        target_r = float(sel["radius_xy"])
        axis = str(sel.get("axis", "z")).lower()
        # Choose the two transverse axes for the radius computation.
        ax_idx = {"x": 0, "y": 1, "z": 2}[axis]
        rad_axes = [i for i in (0, 1, 2) if i != ax_idx]
        zr = sel.get("z_range")
        if zr is not None:
            z0, z1 = float(zr[0]), float(zr[1])
        out: list[int] = []
        for nid, n in nodes.items():
            r = (n[rad_axes[0]] ** 2 + n[rad_axes[1]] ** 2) ** 0.5
            if abs(r - target_r) > tol:
                continue
            if zr is not None and not (z0 - tol <= n[ax_idx] <= z1 + tol):
                continue
            out.append(nid)
        return out

    if "sphere_shell" in sel:
        # Nodes lying on a spherical shell of radius r centred at (cx,cy,cz),
        # within tol_mm. Optional axial_{min,max}_{x|y|z} clips to one
        # hemisphere along the named axis (e.g. axial_min_x=250 keeps only
        # nodes with x >= 250, useful for the forward hemi cap of a hull
        # whose cylinder ends at x=250).
        cx, cy, cz, r = [float(v) for v in sel["sphere_shell"]]
        out: list[int] = []
        for nid, n in nodes.items():
            d = ((n[0] - cx) ** 2 + (n[1] - cy) ** 2 + (n[2] - cz) ** 2) ** 0.5
            if abs(d - r) > tol:
                continue
            ok = True
            for axis_name, idx in (("x", 0), ("y", 1), ("z", 2)):
                amin = sel.get(f"axial_min_{axis_name}")
                amax = sel.get(f"axial_max_{axis_name}")
                if amin is not None and n[idx] < float(amin) - tol:
                    ok = False
                    break
                if amax is not None and n[idx] > float(amax) + tol:
                    ok = False
                    break
            if ok:
                out.append(nid)
        return out

    if "any_of" in sel:
        # Union (logical OR) of a list of sub-selectors. Each sub-selector
        # uses the same grammar; the result is the de-duplicated union.
        seen: set[int] = set()
        for sub in sel["any_of"]:
            for nid in select_nodes(nodes, sub, default_tol=default_tol):
                seen.add(nid)
        return sorted(seen)

    if sel.get("all"):
        return sorted(nodes.keys())

    raise ValueError(f"empty/unknown selector: {sel}")


def write_nset_block(name: str, ids: list[int]) -> str:
    """Format a *NSET block, 8 IDs per line (CalculiX limit is 16, 8 is safer)."""
    lines = [f"*NSET, NSET={name}"]
    for i in range(0, len(ids), 8):
        lines.append(",".join(str(n) for n in ids[i:i + 8]))
    return "\n".join(lines) + "\n"


def parse_mesh_tet_elements(mesh_inp: Path) -> list[tuple[int, str, list[int]]]:
    """Return [(elem_id, type, [n1..nN]), ...] for every C3D4/C3D10 element.

    For C3D10 (10-node quadratic tet) the four corner nodes are the first four
    in the connectivity list; the remaining six are mid-edge nodes. We keep
    only the first four since CCX face definitions for C3D10 are still
    expressed by the corner-face index (S1..S4).
    """
    out: list[tuple[int, str, list[int]]] = []
    cur_type = None
    in_elem = False
    pending_line = ""
    for raw in mesh_inp.read_text().splitlines():
        s = raw.strip()
        if not s or s.startswith("**"):
            continue
        head = s.split(",")[0].strip().upper()
        if head == "*ELEMENT":
            kv = {p.split("=", 1)[0].strip().upper(): p.split("=", 1)[1].strip()
                  for p in s.split(",")[1:] if "=" in p}
            etype = kv.get("TYPE", "").upper()
            if etype in ("C3D4", "C3D10"):
                cur_type = etype
                in_elem = True
            else:
                cur_type = None
                in_elem = False
            pending_line = ""
            continue
        if s.startswith("*"):
            in_elem = False
            cur_type = None
            pending_line = ""
            continue
        if not in_elem or cur_type is None:
            continue
        # Element rows can wrap onto multiple lines if they end with ','
        line = (pending_line + s).strip()
        if line.endswith(","):
            pending_line = line
            continue
        pending_line = ""
        parts = [p.strip() for p in line.split(",") if p.strip()]
        try:
            ints = [int(p) for p in parts]
        except ValueError:
            continue
        eid = ints[0]
        conn = ints[1:]
        # Keep only corner nodes (4 for C3D4, first 4 for C3D10).
        out.append((eid, cur_type, conn[:4]))
    return out


def find_pressure_surface_faces(elements: list[tuple[int, str, list[int]]],
                                 face_node_set: set[int]) -> list[tuple[int, int]]:
    """Return [(elem_id, face_index_1_to_4), ...] for every tet face whose
    three corner nodes are all in face_node_set.

    Tetrahedral face numbering per CCX *SURFACE doc:
        Face 1: nodes (1, 2, 3)        -> conn idx (0, 1, 2)
        Face 2: nodes (1, 4, 2)        -> conn idx (0, 3, 1)
        Face 3: nodes (2, 4, 3)        -> conn idx (1, 3, 2)
        Face 4: nodes (3, 4, 1)        -> conn idx (2, 3, 0)
    Each face has exactly three corner nodes (also true for C3D10 since the
    mid-edge nodes are dropped).
    """
    face_idx_to_nodes = {
        1: (0, 1, 2),
        2: (0, 3, 1),
        3: (1, 3, 2),
        4: (2, 3, 0),
    }
    out: list[tuple[int, int]] = []
    for eid, _etype, conn in elements:
        if len(conn) < 4:
            continue
        for fnum, idxs in face_idx_to_nodes.items():
            n1, n2, n3 = conn[idxs[0]], conn[idxs[1]], conn[idxs[2]]
            if n1 in face_node_set and n2 in face_node_set and n3 in face_node_set:
                out.append((eid, fnum))
    return out


def write_surface_block(name: str, faces: list[tuple[int, int]]) -> str:
    """Emit a *SURFACE, NAME=<name>, TYPE=ELEMENT block listing element faces."""
    lines = [f"*SURFACE, NAME={name}, TYPE=ELEMENT"]
    for eid, fnum in faces:
        lines.append(f"{eid}, S{fnum}")
    return "\n".join(lines) + "\n"


def main(mesh_inp_path: str, meta_json_path: str,
         template_inp_path: str, out_inp_path: str) -> int:
    mesh_inp = Path(mesh_inp_path)
    meta = json.loads(Path(meta_json_path).read_text())
    template = Path(template_inp_path).read_text()

    nodes = parse_mesh_nodes(mesh_inp)
    if not nodes:
        print("ERROR: no *NODE block found in mesh.inp", file=sys.stderr)
        return 1

    selectors = meta.get("selectors") or {}
    if not selectors:
        print("ERROR: meta.json has no 'selectors'", file=sys.stderr)
        return 2

    nset_blocks: list[str] = []
    for name, sel in selectors.items():
        ids = select_nodes(nodes, sel)
        if not ids:
            print(f"ERROR: selector '{name}' matched 0 nodes (geometry/tol mismatch)",
                  file=sys.stderr)
            return 3
        nset_blocks.append(write_nset_block(name, ids))
        print(f"  NSET={name}: {len(ids)} nodes")

    # Optional simple template substitutions
    for placeholder, key in [("__MATERIAL__", "material"),
                              ("__JOBNAME__", "jobname")]:
        if key in meta:
            template = template.replace(placeholder, str(meta[key]))

    volume_elsets = find_volume_elsets(mesh_inp)
    if not volume_elsets:
        print("ERROR: no C3D* element sets found in mesh.inp", file=sys.stderr)
        return 4
    eall_block = build_eall_block(volume_elsets)

    # Pressure surfaces: each entry produces a *SURFACE TYPE=ELEMENT block
    # by intersecting tet faces with a node-selector (same grammar as NSETs).
    pressure_surfaces = meta.get("pressure_surfaces") or {}
    surface_blocks: list[str] = []
    if pressure_surfaces:
        tet_elements = parse_mesh_tet_elements(mesh_inp)
        if not tet_elements:
            print("ERROR: pressure_surfaces requested but no C3D4/C3D10 "
                  "elements found in mesh.inp", file=sys.stderr)
            return 5
        for sname, sel in pressure_surfaces.items():
            face_nodes = set(select_nodes(nodes, sel))
            if not face_nodes:
                print(f"ERROR: pressure_surface '{sname}' matched 0 nodes",
                      file=sys.stderr)
                return 6
            faces = find_pressure_surface_faces(tet_elements, face_nodes)
            if not faces:
                print(f"ERROR: pressure_surface '{sname}' matched 0 element "
                      f"faces ({len(face_nodes)} candidate nodes)",
                      file=sys.stderr)
                return 7
            surface_blocks.append(write_surface_block(sname, faces))
            print(f"  SURFACE={sname}: {len(faces)} element faces "
                  f"(from {len(face_nodes)} nodes)")

    final = (
        mesh_inp.read_text().rstrip()
        + "\n** ---- Eall (auto-built from C3D* ELSETs) ----\n"
        + eall_block
        + "** ---- BC NSETs (from meta.json) ----\n"
        + "".join(nset_blocks)
        + ("** ---- Pressure surfaces (from meta.json) ----\n"
           + "".join(surface_blocks) if surface_blocks else "")
        + "** ---- analysis (from analysis_template.inp) ----\n"
        + template
    )
    Path(out_inp_path).write_text(final)
    print(f"wrote {out_inp_path}: {len(nodes)} nodes, "
          f"NSETs={list(selectors.keys())}, "
          f"Eall = {volume_elsets}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 5:
        print(__doc__, file=sys.stderr)
        sys.exit(64)
    sys.exit(main(*sys.argv[1:5]))
