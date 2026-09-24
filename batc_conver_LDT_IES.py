"""
batc_conver_LDT_IES.py
=======================

Batch convert every EULUMDAT (.ldt) file in an input directory to a
LM-63-2002 Type C IES file — with the same EULUMDAT->LM-63 C-plane
convention shift as the web app — and export the photometric polar curve
as a PNG image next to it.

This script is only a CLI driver: all conversion/geometry logic is reused
from the project's existing code (no project files are modified).

    app.py                     -> generate_c_angles(), generate_gamma_angles()
    ldt_tools.symmetry         -> to_full_beam()
    ldt_tools.ies_writer       -> write_ies()
    pyldt                      -> LdtReader

For each input file `<name>.ldt` a folder named `<name>` is created next
to the source file and receives:

    <name>.ies          converted IES (absolute candela, C-plane shift 90 deg)
    <name>_polar.png    polar curve (bilateral cut for the default C planes)

Usage
-----
    python batc_conver_LDT_IES.py [INPUT_DIR] [--output-dir DIR] [--rotation DEG]

  INPUT_DIR        directory scanned for .ldt files (default: current dir);
                     the scan is recursive, sub-folders are included
  --output-dir     where the per-luminaire folders are placed
                   (default: same folder as each source .ldt)
  --rotation       C-plane rotation in degrees applied before export
                   (default: 90, the standard EULUMDAT->LM-63 shift)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pyldt import LdtReader

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app import generate_c_angles, generate_gamma_angles      # noqa: E402
from ldt_tools import ies_writer, symmetry                    # noqa: E402


def polar_curve_image(
    c_angles: list,
    gamma_angles: list,
    intensities: list,
    isym: int,
    output_path,
    title: str,
    plane_indices: list | None = None,
    dpi: int = 150,
    figsize=(7.5, 7.5),
    reuse_figure: bool = False,
) -> str:
    """
    Render the photometric polar curve as a PNG, reproducing the web app's
    chart (bilateral cut: selected C-plane on the right, its C+180 mirror on
    the left; gamma 0 = nadir at the top, clockwise).

    `plane_indices`: the C-plane indices to draw. Defaults to the web app's
    default set (first plane, the mc/4 plane and the mc/2 plane).

    `reuse_figure`: keep the matplotlib figure alive between calls (per-plane
    batch rendering) to cut per-image overhead.
    """
    import numpy as np
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    global _POLAR_FIG_CACHE
    if _POLAR_FIG_CACHE is None:
        _POLAR_FIG_CACHE = {}

    mc = len(c_angles)

    def mirror_index(index: int) -> int:
        if mc <= 1 or isym == 1:
            return index
        target = (c_angles[index] + 180.0) % 360.0
        best_index, best_dist = index, float("inf")
        for i, c in enumerate(c_angles):
            diff = abs(c - target) % 360.0
            dist = min(diff, 360.0 - diff)
            if dist < best_dist:
                best_dist, best_index = dist, i
        return best_index

    defaults = sorted({0, mc // 4 if mc > 1 else 0, mc // 2 if mc > 2 else mc // 2})
    if plane_indices is not None:
        defaults = sorted(set(int(i) for i in plane_indices))

    if reuse_figure:
        cache_key = tuple(figsize)
        if cache_key not in _POLAR_FIG_CACHE:
            fig, ax = plt.subplots(figsize=figsize, subplot_kw={"projection": "polar"})
            _POLAR_FIG_CACHE[cache_key] = (fig, ax)
        else:
            fig, ax = _POLAR_FIG_CACHE[cache_key]
            ax.cla()
    else:
        fig, ax = plt.subplots(figsize=figsize, subplot_kw={"projection": "polar"})

    ax.set_theta_direction(-1)          # clockwise, like Plotly "clockwise"
    ax.set_theta_offset(np.pi / 2)      # rotation 90 -> gamma 0 (nadir) at top
    ax.set_theta_zero_location("N")
    ax.set_ylim(0, None)

    colours = plt.rcParams["axes.prop_cycle"].by_key()["color"]

    for colour_index, index in enumerate(defaults):
        if index >= len(intensities):
            continue
        mirror = mirror_index(index)
        right_row = [float(v) for v in intensities[index]]
        left_row = [float(v) for v in intensities[mirror]]

        theta = [np.radians(360.0 - g) for g in reversed(gamma_angles)]
        theta += [np.radians(g) for g in gamma_angles]
        r = list(reversed(left_row)) + list(right_row)
        theta.append(theta[0])          # close the curve
        r.append(r[0])

        label = (
            f"C {c_angles[index]:g}\N{DEGREE SIGN}"
            if mirror == index
            else f"C {c_angles[index]:g}\N{DEGREE SIGN} / C {c_angles[mirror]:g}\N{DEGREE SIGN}"
        )

        ax.plot(
            theta, r,
            color=colours[colour_index % len(colours)],
            linewidth=1.6,
            label=label,
        )

    ax.grid(True, linestyle="--", alpha=0.4)
    ax.set_title(title, pad=24, fontsize=12)
    ax.legend(loc="upper right", bbox_to_anchor=(1.28, 1.0), frameon=False, fontsize=9)

    fig.savefig(output_path, dpi=dpi, bbox_inches="tight")
    if not reuse_figure:
        plt.close(fig)
    return str(output_path)


# Keyed by figsize -> (figure, polar axes) when `reuse_figure` is used.
_POLAR_FIG_CACHE = None


def convert_file(ldt_path: Path, output_dir: Path, rotation_deg: float) -> Path:
    """Convert a single .ldt: IES export + polar curve PNG. Returns output folder."""
    out_folder = output_dir / ldt_path.stem
    out_folder.mkdir(parents=True, exist_ok=True)

    ldt = LdtReader.read(ldt_path)

    # Same pipeline as app.py /api/export/ies: normalise to full-beam
    # (ISYM=0) so the horizontal angle set legitimately spans 0-360 deg,
    # then apply the user-selectable C-plane rotation before writing.
    full_ldt = symmetry.to_full_beam(ldt)

    ies_path = out_folder / f"{ldt_path.stem}.ies"
    ies_writer.write_ies(full_ldt, ies_path, rotation_deg=rotation_deg)

    # Polar curve uses the same data the web app plots (raw cd/klm matrix).
    intensities = [[float(v) for v in row] for row in ldt.intensities]
    c_angles = generate_c_angles(ldt, len(intensities))
    gamma_angles = generate_gamma_angles(ldt, len(intensities[0]) if intensities else 0)

    header = ldt.header
    name = getattr(header, "luminaire_name", None) or ldt_path.stem
    title = f"{name}  ({ldt_path.name})"

    png_path = out_folder / f"{ldt_path.stem}_polar.png"
    polar_curve_image(c_angles, gamma_angles, intensities, int(getattr(header, "isym", 0)), png_path, title)

    print(f"[OK] {ldt_path.name}")
    print(f"     -> {ies_path.name}  (C-planes={len(c_angles)}, gamma={len(gamma_angles)}, rotation={rotation_deg:g} deg)")
    print(f"     -> {png_path.name}")
    return out_folder


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch convert .ldt files to .ies + polar PNG.")
    parser.add_argument("input_dir", nargs="?", default=".", help="directory scanned for .ldt files (default: current dir)")
    parser.add_argument("--output-dir", default=None, help="where per-luminaire folders are created (default: alongside each .ldt)")
    parser.add_argument("--rotation", type=float, default=90.0, help="C-plane rotation in degrees before export (default: 90)")
    args = parser.parse_args()

    input_dir = Path(args.input_dir).resolve()
    if not input_dir.is_dir():
        print(f"[ERROR] Input directory not found: {input_dir}")
        return 1

    ldt_files = sorted(input_dir.rglob("*.ldt"))

    if not ldt_files:
        print(f"[INFO] No .ldt files found in {input_dir}")
        return 0

    print(f"[INFO] Found {len(ldt_files)} .ldt file(s) in {input_dir}")
    print(f"[INFO] C-plane rotation before export: {args.rotation:g} deg")

    ok, failed = 0, 0
    for ldt_path in ldt_files:
        output_dir = Path(args.output_dir).resolve() if args.output_dir else ldt_path.parent
        try:
            convert_file(ldt_path, output_dir, args.rotation)
            ok += 1
        except Exception as error:
            failed += 1
            print(f"[FAIL] {ldt_path.name}: {error}")

    print(f"[DONE] {ok} converted, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())