"""
batc_IES_READY.py
=================

Batch-processing step 2 (IES-READY build):

1. Recursively scan a source directory (default: ./LDT-READY) for every
   .ies file.
2. Parse each IES with the project's own `ldt_tools.ies_reader` to extract
   the [LUMINAIRE] name, total lumens and total input watts.
3. Generate the photometric polar curve (PNG) for that IES using the same
   chart logic as the web app (`batc_conver_LDT_IES.polar_curve_image`).
4. Add a candela heat-map (C-plane x gamma) image for the same luminaire.
5. Create a folder under IES-READY named with the luminaire identity:

       "<[LUMINAIRE]>_<total lumens>lm_<total watts>W"

   and place inside it:
       - the .ies file
       - the matching .ldt file, IF one exists next to the source IES;
         otherwise a .ldt is generated from the IES data itself
       - the candela heat-map .png
       - the polar curve .png  (single plane when the IES has one
         horizontal angle) and, for multiple horizontal angles, a `polar/`
         sub-folder next to the .ies/.ldt containing:
           - the vertical polar curves at the 4 cardinal C-planes
             (C0/90/180/270, in the file when present; each image already
             includes its C+180 mirror)
           - the horizontal curves (candela vs C-plane) at the edge gamma
             angles (0, 90, 180 when present, plus the file maximum)

   `--filter-only` loops over an existing IES-READY tree and trims any
   `polar/` folder back to exactly this set (deleting the other per-plane
   curves and adding the missing horizontal ones) without re-exporting.

Reuses existing project code only (no project files are modified):

    ldt_tools.ies_reader          -> read_ies()
    ldt_tools.convention          -> expand_ies_to_full()
    batc_conver_LDT_IES           -> polar_curve_image()
    pyldt.model / pyldt.LdtWriter -> Ldt/LdtHeader/write()

Usage
-----
    python batc_IES_READY.py [SRC_DIR] [--ies-ready-dir DIR]
    python batc_IES_READY.py --filter-only [SRC_DIR] [--ies-ready-dir DIR]

  SRC_DIR           directory scanned recursively for .ies files
                    (default: ./LDT-READY)
  --ies-ready-dir   where the IES-READY folder is created
                    (default: <SRC_DIR's parent>/IES-READY)
  --filter-only     trim existing IES-READY folders to the cardinal-4
                    polar set + horizontal curves (no re-export)
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from ldt_tools.ies_reader import read_ies                    # noqa: E402
from ldt_tools.convention import expand_ies_to_full          # noqa: E402
from batc_conver_LDT_IES import polar_curve_image            # noqa: E402


def _sanitize_name(value: str) -> str:
    """Turn an arbitrary [LUMINAIRE] value + numbers into a safe folder name."""
    cleaned = "".join(c if c not in '<>:"/\\|?*' else "_" for c in value)
    cleaned = " ".join(cleaned.split()).strip(" .")
    return cleaned or "UNKNOWN"


def polar_curve_from_ies(ies_path: Path, png_path: Path, ies: dict,
                         plane_indices: list | None = None,
                         reuse_figure: bool = False,
                         dpi: int = 150,
                         figsize=(7.5, 7.5)) -> str:
    """Render the polar (candlepower) curve of a parsed IES to a PNG."""
    luminaire = ies["header_fields"].get("LUMINAIRE") or ies_path.stem
    title = f"{luminaire}  ({ies_path.name})"

    return polar_curve_image(
        list(ies["h_angles"]),
        list(ies["v_angles"]),
        ies["candela"],
        isym=0,
        output_path=png_path,
        title=title,
        plane_indices=plane_indices,
        dpi=dpi,
        figsize=figsize,
        reuse_figure=reuse_figure,
    )


def heatmap_from_ies(ies_path: Path, png_path: Path, ies: dict) -> str:
    """
    Render a candela heat-map (horizontal angle on x, gamma on y) of the IES
    as a PNG. The matrix is expanded to a full 0-360 deg C range first.
    """
    import numpy as np
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    full_angs, full_rows = expand_ies_to_full(ies["h_angles"], ies["candela"])
    z = np.array(full_rows, dtype=float).T  # rows=gamma, cols=C-plane

    luminaire = ies["header_fields"].get("LUMINAIRE") or ies_path.stem
    title = f"{luminaire}  ({ies_path.name})"

    fig, ax = plt.subplots(figsize=(9.5, 5.5))
    im = ax.imshow(
        z,
        aspect="auto",
        origin="lower",
        extent=[full_angs[0], full_angs[-1], ies["v_angles"][0], ies["v_angles"][-1]],
        cmap="RdYlBu_r",
    )
    ax.set_xlabel("C-plane angle (deg)")
    ax.set_ylabel("Gamma angle (deg)")
    ax.set_ylim(ies["v_angles"][0], ies["v_angles"][-1])
    ax.set_title(title, pad=12, fontsize=11)
    colorbar = fig.colorbar(im, ax=ax, shrink=0.9)
    colorbar.set_label("Candela (cd)")

    fig.tight_layout()
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return str(png_path)


def create_ldt_from_ies(ies_path: Path, ies: dict, out_path: Path) -> Path:
    """
    Build an EULUMDAT .ldt from IES data (used when no source .ldt exists).
    The IES candles are converted back to cd/klm and stored as a full-beam
    (ISYM=0) matrix, mirroring the LDT->IES conversion done by the app.
    """
    from pyldt import LdtWriter
    from pyldt.model import Ldt, LdtHeader

    hf = ies["header_fields"]

    num_lamps = int(ies["num_lamps"] or 1)
    lumens = ies["total_lumens"]
    if not lumens or lumens <= 0:
        lumens = num_lamps * float(ies["lumens_per_lamp"] or 0.0)
    if not lumens or lumens <= 0:
        lumens = 1000.0
    watts = float(ies["input_watts"] or 0.0)

    scale = lumens / 1000.0
    full_angs, full_rows = expand_ies_to_full(ies["h_angles"], ies["candela"])
    intensities = [[v / scale for v in row] for row in full_rows]

    mc = len(full_angs)
    ng = len(ies["v_angles"])
    dc = 360.0 / mc if mc else 0.0

    g = [float(a) for a in ies["v_angles"]]
    gamma_steps = [b - a for a, b in zip(g, g[1:])]
    uniform_gamma = (
        len(gamma_steps) > 0 and all(abs(s - gamma_steps[0]) < 1e-6 for s in gamma_steps)
    )

    h = LdtHeader()
    h.company = hf.get("MANUFAC", "")
    h.ityp = 0
    h.isym = 0
    h.mc = mc
    h.dc = dc
    h.ng = ng
    h.dg = gamma_steps[0] if uniform_gamma else 0.0
    h.c_angles = [float(a) for a in full_angs]
    h.g_angles = list(g) if not uniform_gamma else []
    h.report_number = hf.get("TEST", "")
    h.luminaire_name = hf.get("LUMINAIRE", ies_path.stem)
    h.luminaire_number = hf.get("LUMCAT", "") or hf.get("LUMINAIRE", "")
    h.file_name = ies_path.name
    h.date_user = hf.get("ISSUEDATE", "")

    units = int(ies["units_type"] or 2)
    to_mm = 304.8 if units == 1 else 1000.0
    h.length = float(ies["length"] or 0.0) * to_mm
    h.width = float(ies["width"] or 0.0) * to_mm
    h.height = float(ies["height"] or 0.0) * to_mm
    h.length_lum_area = h.length
    h.width_lum_area = h.width

    h.n_sets = 1
    h.num_lamps = [1]
    h.lamp_types = [hf.get("LAMP", "")]
    h.lamp_flux = [lumens]
    h.lamp_watt = [watts]

    ldt = Ldt(header=h, intensities=intensities)
    LdtWriter.write(ldt, out_path, overwrite=True)
    return out_path


def find_matching_ldt(ies_path: Path) -> Path | None:
    """
    Locate the .ldt belonging to an IES, if any. Looks for a same-stem .ldt
    in the IES folder or its parents (covers the "<name>/<name>.ies" layout
    where the .ldt sits next to that folder), or a unique .ldt in the IES's
    own folder.
    """
    stem = ies_path.stem.lower()

    for level, folder in enumerate([
        Path(ies_path).parent,
        Path(ies_path).parent.parent,
        Path(ies_path).parent.parent.parent,
    ]):
        same_stem = [p for p in folder.glob("*.ldt") if p.stem.lower() == stem]
        if same_stem:
            return same_stem[0]
        if level == 0:
            own_ldts = list(folder.glob("*.ldt"))
            if len(own_ldts) == 1:
                return own_ldts[0]
    return None


def _sha1(path: Path) -> str:
    hasher = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _same_file(a: Path, b: Path) -> bool:
    if not a.exists() or not b.exists() or a.stat().st_size != b.stat().st_size:
        return False
    return _sha1(a) == _sha1(b)


def _copy_same(src: Path, dst: Path) -> bool:
    """Copy src -> dst if they differ; returns True when the file is present."""
    if _same_file(src, dst):
        return False  # already identical, nothing to write
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def _fmt_angle(angle) -> str:
    """Short label for a file/section name (0 -> \"0\", 90.5 -> \"90.5\")."""
    return str(round(float(angle), 3)).rstrip("0").rstrip(".")


CARDINAL_C_PLANES = (0.0, 90.0, 180.0, 270.0)


def cardinal_c_indices(h_angles: list) -> list:
    """
    Indices of the 4 cardinal C-planes actually present in an IES horizontal
    list (C0/90/180/270). Partial files (0-90 only) return just C0 & C90;
    each vertical curve already includes its C+180 mirror by construction.
    """
    hs = [float(a) for a in h_angles]
    steps = [abs(b - a) for a, b in zip(hs, hs[1:]) if b > a]
    tol = (min(steps) / 2.0) if steps else 360.0
    indices = []
    for target in CARDINAL_C_PLANES:
        best = min(range(len(hs)), key=lambda i: min(abs(hs[i] - target), 360.0 - abs(hs[i] - target)))
        if min(abs(hs[best] - target), 360.0 - abs(hs[best] - target)) <= tol:
            indices.append(best)
    return sorted(set(indices))


def edge_gammas(v_angles: list) -> list:
    """
    Edge vertical (gamma) angles for the horizontal-cross-section curves:
    gamma 0, 90, 180 when present, plus the file's maximum gamma.
    """
    vs = sorted(float(a) for a in v_angles)
    targets = []
    for g in (0.0, 90.0, 180.0):
        if g <= vs[-1] + 1e-6 and any(abs(v - g) < 1e-6 for v in vs):
            targets.append(g)
    if not any(abs(v - vs[-1]) < 1e-6 for v in targets):
        targets.append(vs[-1])
    return targets


def horizontal_curve_from_ies(ies_path: Path, png_path: Path, ies: dict,
                              gamma_deg: float, dpi: int = 140) -> str:
    """
    Render a horizontal polar curve: candela vs C-plane angle (expanded to a
    full 0-360 deg circle) at a fixed gamma (vertical) angle.
    """
    import numpy as np
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    full_angs, full_rows = expand_ies_to_full(ies["h_angles"], ies["candela"])
    vs = [float(a) for a in ies["v_angles"]]
    idx = min(range(len(vs)), key=lambda i: abs(vs[i] - gamma_deg))

    r = [float(row[idx]) for row in full_rows]
    theta = [np.radians(float(a)) for a in full_angs]
    theta.append(theta[0])
    r.append(r[0])

    luminaire = ies["header_fields"].get("LUMINAIRE") or ies_path.stem

    fig, ax = plt.subplots(figsize=(6.0, 6.0), subplot_kw={"projection": "polar"})
    ax.set_theta_direction(-1)
    ax.set_theta_zero_location("N")
    ax.set_ylim(0, None)

    ax.plot(theta, r, linewidth=1.7, color="#d62728",
            label=f"Gamma {gamma_deg:g}\N{DEGREE SIGN}")
    ax.grid(True, linestyle="--", alpha=0.4)
    ax.set_title(
        f"{luminaire}  ({ies_path.name})\nhorizontal \u00b7 gamma {gamma_deg:g}\N{DEGREE SIGN}",
        pad=26, fontsize=11,
    )
    ax.legend(loc="upper right", bbox_to_anchor=(1.28, 1.0), frameon=False, fontsize=9)

    fig.tight_layout()
    fig.savefig(png_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return str(png_path)


def _base_dest_ies(folder: Path, ies_path: Path) -> Path:
    """Destination IES path in `folder`, adding a "(n)" suffix on real collisions."""
    dst = folder / ies_path.name
    if dst.exists() and not _same_file(ies_path, dst):
        stem, suffix = ies_path.stem, ies_path.suffix
        n = 2
        while (folder / f"{stem} ({n}){suffix}").exists():
            n += 1
        dst = folder / f"{stem} ({n}){suffix}"
    return dst


def plan_assignments(ies_files: list, ies_ready: Path) -> list:
    """
    Sequentially assign each IES file its destination luminaire folder
    ("<[LUMINAIRE]>_<lumens>lm_<watts>W", with "(n)" suffixes on real
    multiplicity). Deterministic on the sorted source list, so re-runs
    update the exact same folders.
    """
    used: dict = {}
    pairs = []
    for ies_path in ies_files:
        ies = read_ies(str(ies_path))
        luminaire = (ies["header_fields"].get("LUMINAIRE") or ies_path.stem).strip()
        lumens = ies["total_lumens"] or 0.0
        watts = ies["input_watts"] or 0.0

        key = f"{_sanitize_name(luminaire)}_{lumens:.0f}lm_{watts:.0f}W"
        if key in used:
            used[key] += 1
            folder = ies_ready / f"{key} ({used[key]})"
        else:
            used[key] = 1
            folder = ies_ready / key
        pairs.append((ies_path, folder))
    return pairs


def process_ies(ies_path: Path, folder: Path) -> dict:
    ies = read_ies(str(ies_path))
    folder.mkdir(parents=True, exist_ok=True)

    luminaire = (ies["header_fields"].get("LUMINAIRE") or ies_path.stem).strip()
    lumens = ies["total_lumens"] or 0.0
    watts = ies["input_watts"] or 0.0

    # --- IES ---
    dst_ies = _base_dest_ies(folder, ies_path)
    _copy_same(ies_path, dst_ies)

    # --- LDT: keep the original when present, otherwise generate one ---
    ldt_src = find_matching_ldt(ies_path)
    ldt_copy = None
    ldt_generated = None
    ldt_dst = folder / f"{ies_path.stem}.ldt"
    if ldt_src is not None:
        ldt_copy = folder / ldt_src.name
        _copy_same(ldt_src, ldt_copy)
        ldt_dst = ldt_copy
    else:
        ldt_generated = create_ldt_from_ies(ies_path, ies, ldt_dst)

    # --- Candela heat-map ---
    heatmap_path = folder / f"{dst_ies.stem}_heatmap.png"
    heatmap_from_ies(ies_path, heatmap_path, ies)

    # --- Polar curves ---
    n_h = len(ies["h_angles"])
    polar_files = []
    if n_h > 1:
        # Summary chart in the folder root ...
        main_png = folder / f"{dst_ies.stem}_polar.png"
        polar_files.append(polar_curve_from_ies(ies_path, main_png, ies))
        # ... vertical curves at the 4 cardinal C-planes, collected in `polar/`.
        polar_dir = folder / "polar"
        polar_dir.mkdir(parents=True, exist_ok=True)
        for idx in cardinal_c_indices(ies["h_angles"]):
            angle = ies["h_angles"][idx]
            per_png = polar_dir / f"{dst_ies.stem}_C{_fmt_angle(angle)}_polar.png"
            polar_files.append(polar_curve_from_ies(ies_path, per_png, ies, plane_indices=[idx], reuse_figure=True, dpi=110, figsize=(6.0, 6.0)))
        # ... horizontal curves (candela vs C-plane) at the edge gamma angles.
        for gamma in edge_gammas(ies["v_angles"]):
            horiz_png = polar_dir / f"{dst_ies.stem}_G{_fmt_angle(gamma)}_horizontal.png"
            polar_files.append(horizontal_curve_from_ies(ies_path, horiz_png, ies, gamma))
    else:
        single_png = folder / f"{dst_ies.stem}_polar.png"
        polar_files.append(polar_curve_from_ies(ies_path, single_png, ies))

    return {
        "source_ies": ies_path,
        "luminaire": luminaire,
        "lumens": lumens,
        "watts": watts,
        "folder": folder,
        "ies": dst_ies,
        "ldt": ldt_copy,
        "ldt_generated": ldt_generated is not None,
        "heatmap": heatmap_path,
        "polar_files": polar_files,
        "n_h_angles": n_h,
    }


def _process_job(job) -> object:
    """Top-level worker for ProcessPoolExecutor: process one (ies_path, folder)."""
    ies_path, folder = job
    try:
        return process_ies(ies_path, folder)
    except Exception as error:
        return error


_PER_CURVE = re.compile(r"^(.+?)_C([0-9.]+)_polar\.png$")


def filter_existing_folders(ies_ready: Path) -> int:
    """
    Filter pass: loop over the existing IES-READY folders WITHOUT re-exporting.
    Keeps only the cardinal C-plane vertical curves in each `polar/` folder,
    deletes every other per-plane curve, and adds the missing horizontal
    (candela vs C-plane) curves at the edge gamma angles.
    """
    processed = 0
    total_deleted = 0
    total_added = 0
    for folder in sorted(p for p in ies_ready.iterdir() if p.is_dir()):
        polar_dir = folder / "polar"
        ies_files = sorted(folder.glob("*.ies"))
        if not polar_dir.is_dir() or not ies_files:
            continue

        ies_path = ies_files[0]
        ies = read_ies(str(ies_path))
        keep_angles = {float(ies["h_angles"][i]) for i in cardinal_c_indices(ies["h_angles"])}

        deleted = 0
        for png in polar_dir.glob("*_C*_polar.png"):
            match = _PER_CURVE.match(png.name)
            if not match:
                continue
            if float(match.group(2)) not in keep_angles:
                png.unlink()
                deleted += 1

        added = []
        for gamma in edge_gammas(ies["v_angles"]):
            horiz_png = polar_dir / f"{ies_path.stem}_G{_fmt_angle(gamma)}_horizontal.png"
            if not horiz_png.exists():
                horizontal_curve_from_ies(ies_path, horiz_png, ies, gamma)
                added.append(horiz_png.name)

        total_deleted += deleted
        total_added += len(added)
        print(f"[OK] {Path(folder).name}  ->  kept {len(keep_angles)} vertical, deleted {deleted}, added horizontal {', '.join(added) if added else '-'}")
        processed += 1
    print(f"[DONE] {processed} folder(s) filtered: {total_deleted} per-plane PNG(s) deleted, {total_added} horizontal curve(s) added.")
    return processed


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the IES-READY folder (IES + LDT + polar curves + heat-map).")
    parser.add_argument("src_dir", nargs="?", default="LDT-READY", help="directory scanned recursively for .ies files (default: LDT-READY)")
    parser.add_argument("--ies-ready-dir", default=None, help="output IES-READY folder (default: <src_dir parent>/IES-READY)")
    parser.add_argument("--filter-only", action="store_true", help="loop existing IES-READY folders: delete non-cardinal per-plane curves, add missing horizontal curves (no re-export)")
    args = parser.parse_args()

    src_dir = Path(args.src_dir).resolve()
    if args.filter_only:
        ies_ready = Path(args.ies_ready_dir).resolve() if args.ies_ready_dir else src_dir.parent / "IES-READY"
        if not ies_ready.is_dir():
            print(f"[ERROR] IES-READY folder not found: {ies_ready}")
            return 1
        filter_existing_folders(ies_ready)
        return 0

    if not src_dir.is_dir():
        print(f"[ERROR] Source directory not found: {src_dir}")
        return 1

    ies_ready = Path(args.ies_ready_dir).resolve() if args.ies_ready_dir else src_dir.parent / "IES-READY"
    ies_ready.mkdir(parents=True, exist_ok=True)

    ies_files = sorted(src_dir.rglob("*.ies"))
    if not ies_files:
        print(f"[INFO] No .ies files found in {src_dir}")
        return 0

    print(f"[INFO] Found {len(ies_files)} .ies file(s) in {src_dir}")
    print(f"[INFO] Output folder: {ies_ready}")

    assignments = plan_assignments(ies_files, ies_ready)

    n_jobs = min((os.cpu_count() or 1), len(assignments))
    print(f"[INFO] Processing {len(assignments)} luminaire(s) with {n_jobs} job(s)...")

    ok, failed = 0, 0
    results = []
    if n_jobs <= 1:
        for ies_path, folder in assignments:
            try:
                results.append(process_ies(ies_path, folder))
            except Exception as error:
                failed += 1
                print(f"[FAIL] {ies_path.name}: {error}")
    else:
        with ProcessPoolExecutor(max_workers=n_jobs) as pool:
            for ies_path, result in zip(assignments, pool.map(_process_job, assignments)):
                if isinstance(result, Exception):
                    failed += 1
                    print(f"[FAIL] {ies_path[0].name}: {result}")
                else:
                    results.append(result)

    for result in results:
        ok += 1
        ldt_note = f"ldt={Path(result['ldt']).name}" if result["ldt"] else ("ldt=generated" if result["ldt_generated"] else "ldt=none")
        print(f"[OK] {result['luminaire']}  {result['lumens']:.0f}lm  {result['watts']:.0f}W")
        print(f"     -> {Path(result['folder']).relative_to(ies_ready)}  (ies={Path(result['ies']).name}, {ldt_note}, heatmap, {result['n_h_angles']} C-plane polar(s))")

    print(f"[DONE] {ok} processed, {failed} failed")
    print(f"[INFO] Created {len(assignments)} luminaire folder(s) in {ies_ready}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())