"""
ldt_tools.ies_writer
=====================

Converts a (full-beam) `pyldt.model.Ldt` object into an IESNA LM-63-2002
Type C photometric file (.ies).

File layout written (LM-63-2002, TILT=NONE, absolute photometry):

    IESNA:LM-63-2002
    [TEST] ...
    [MANUFAC] ...
    [LUMCAT] ...
    [LUMINAIRE] ...
    [LAMP] ...
    [_SC_SOURCE] Converted from EULUMDAT by SC Photometric Toolkit
    TILT=NONE
    <n_lamps> <lumens_per_lamp> <multiplier> <n_v_angles> <n_h_angles> <photometric_type> <units_type> <width> <length> <height>
    <ballast_factor> <bf_lamp_photometric_factor> <input_watts>
    <vertical angles ...>
    <horizontal angles ...>
    <candela values, one block of n_v_angles values per horizontal angle>

Equations / conversions
------------------------
EULUMDAT candela values are stored per 1000 lm of rated luminaire output
("cd/klm"). LM-63 candela values are absolute (cd at the luminaire's actual
output). The conversion is:

    total_lumens   = sum_i( num_lamps_i * lamp_flux_i )     (over lamp sets)
    candela_abs    = candela_per_klm * (total_lumens / 1000)

We write `candela_multiplier = 1` and pre-multiply every candela value so
the file is directly usable ("absolute photometry"), with `lumens_per_lamp`
set to the aggregate `total_lumens` and `num_lamps = 1` (the whole luminaire
treated as one aggregate source, which is the common convention used by
LDT->IES converters when the source file has mixed lamp sets).

Photometric type: 1 = Type C (this module always emits Type C; convert the
LDT to full-beam ISYM=0 first via `ldt_tools.symmetry.to_full_beam` so the
horizontal-angle list legitimately spans 0-360 deg).

Units type: 2 = meters. Luminous dimensions are read from the EULUMDAT
header (mm) and converted to meters.
"""

from __future__ import annotations

from typing import Optional

from pyldt.model import Ldt


def _fmt(x: float) -> str:
    return f"{float(x):.3f}".rstrip("0").rstrip(".") if isinstance(x, float) else str(x)


def _fmt_num(x: float) -> str:
    # IES readers are picky about at least one decimal on floats; keep it simple & safe
    return f"{float(x):.4f}"


def total_lumens(ldt: Ldt) -> float:
    h = ldt.header
    n = max(1, h.n_sets)
    num_lamps = (h.num_lamps + [1] * n)[:n]
    flux = (h.lamp_flux + [0.0] * n)[:n]
    total = sum((num_lamps[i] or 1) * (flux[i] or 0.0) for i in range(n))
    return total if total > 0 else 1000.0  # fallback: treat matrix as already-absolute cd/klm == cd


def total_watts(ldt: Ldt) -> float:
    h = ldt.header
    n = max(1, h.n_sets)
    num_lamps = (h.num_lamps + [1] * n)[:n]
    watt = (h.lamp_watt + [0.0] * n)[:n]
    return sum((num_lamps[i] or 1) * (watt[i] or 0.0) for i in range(n))


def write_ies(
    ldt: Ldt,
    path: str,
    *,
    manufacturer: Optional[str] = None,
    luminaire_catalog: Optional[str] = None,
    test_report: Optional[str] = None,
) -> str:
    """
    Write `ldt` (expected to already be full-beam / ISYM=0 — call
    `ldt_tools.symmetry.to_full_beam()` first if unsure) as an IES LM-63-2002
    Type C file. Returns the path written.
    """
    h = ldt.header
    mc, ng = h.mc, h.ng

    if len(ldt.intensities) != mc or any(len(r) != ng for r in ldt.intensities):
        raise ValueError(
            f"Intensity matrix must be [{mc} x {ng}], got "
            f"[{len(ldt.intensities)} x {len(ldt.intensities[0]) if ldt.intensities else 0}]"
        )

    lumens = total_lumens(ldt)
    watts = total_watts(ldt)
    scale = lumens / 1000.0

    v_angles = h.g_angles if (h.g_angles and len(h.g_angles) == ng) else [i * h.dg for i in range(ng)]
    h_angles = h.c_angles if (h.c_angles and len(h.c_angles) == mc) else [i * h.dc for i in range(mc)]

    width_m = (h.width_lum_area or h.width or 0.0) / 1000.0
    length_m = (h.length_lum_area or h.length or 0.0) / 1000.0
    height_m = (h.height or 0.0) / 1000.0

    lines = []
    lines.append("IESNA:LM-63-2002")
    lines.append(f"[TEST] {test_report or h.report_number or 'N/A'}")
    lines.append(f"[MANUFAC] {manufacturer or h.company or 'N/A'}")
    lines.append(f"[LUMCAT] {luminaire_catalog or h.luminaire_number or 'N/A'}")
    lines.append(f"[LUMINAIRE] {h.luminaire_name or 'N/A'}")
    lamp_type = h.lamp_types[0] if h.lamp_types else "N/A"
    lines.append(f"[LAMP] {lamp_type}")
    lines.append("[_SC_SOURCE] Converted from EULUMDAT (.ldt) by SC Photometric Toolkit")
    lines.append("[_SC_ORIGINAL_ISYM] see conversion log")
    lines.append("TILT=NONE")

    # Line 10
    lines.append(
        " ".join(
            [
                "1",                     # number of lamps (aggregated)
                _fmt_num(lumens),        # lumens per lamp (aggregate)
                "1",                     # candela multiplier (pre-applied below)
                str(ng),                 # number of vertical angles
                str(mc),                 # number of horizontal angles
                "1",                     # photometric type: 1 = Type C
                "2",                     # units type: 2 = meters
                _fmt_num(width_m),
                _fmt_num(length_m),
                _fmt_num(height_m),
            ]
        )
    )

    # Line 11: ballast factor, ballast-lamp photometric factor, input watts
    lines.append(f"1.000 1.000 {_fmt_num(watts)}")

    # Vertical (gamma) angles
    lines.append(" ".join(_fmt_num(a) for a in v_angles))

    # Horizontal (C) angles
    lines.append(" ".join(_fmt_num(a) for a in h_angles))

    # Candela values: one block of `ng` values per horizontal angle, in order
    for row in ldt.intensities:
        scaled = [v * scale for v in row]
        lines.append(" ".join(_fmt_num(v) for v in scaled))

    text = "\n".join(lines) + "\n"
    with open(path, "w", encoding="ascii", errors="replace") as f:
        f.write(text)

    return path
