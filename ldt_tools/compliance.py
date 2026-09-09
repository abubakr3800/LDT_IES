"""
ldt_tools.compliance
======================

Detailed A-vs-B photometric compliance comparison between an EULUMDAT (.ldt)
file and a reference/target photometric file (.ies or .ldt).

Method
------
1. Both files are reduced to the common "photometric solid" representation
   used by `ldt_tools.photometry` (c_angles, g_angles, ABSOLUTE candela).
   - LDT candela (cd/klm) is scaled to absolute cd using its own total
     rated lumens (ldt_tools.ies_writer.total_lumens).
   - IES candela is already absolute (multiplier pre-applied by the reader).
2. Sample BOTH solids on a common, regular (C, gamma) grid using bilinear
   interpolation (`photometry.candela_at`), so files with different angular
   resolutions can be compared directly.
3. Compute, per sample point:  delta = A - B,  pct_delta = delta / B * 100
   (B = reference/target). Aggregate: max |pct_delta|, RMS % deviation,
   mean % deviation, count of points outside a tolerance band.
4. Compute summary metrics for each file independently (total flux via
   zonal integration, peak candela, beam angle at C0/C90, DFF) and diff them.
5. Issue an overall PASS/WARN/FAIL verdict against a configurable tolerance
   (default +-10%, a common photometric-file QA tolerance; not a specific
   regulatory standard — expose as a parameter so it can be set to match
   whatever spec the user needs, e.g. EN 13032-4 file-consistency checks).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from pyldt.model import Ldt

from . import photometry
from .ies_writer import total_lumens as ldt_total_lumens


@dataclass
class Solid:
    label: str
    c_angles: List[float]
    g_angles: List[float]
    candela: List[List[float]]   # candela[c_idx][g_idx], absolute cd
    total_lumens: Optional[float] = None


def solid_from_ldt(ldt: Ldt, label: str = "LDT") -> Solid:
    lumens = ldt_total_lumens(ldt)
    scale = lumens / 1000.0
    candela = [[v * scale for v in row] for row in ldt.intensities]
    c_angles = ldt.header.c_angles or [i * ldt.header.dc for i in range(ldt.header.mc)]
    g_angles = ldt.header.g_angles or [i * ldt.header.dg for i in range(ldt.header.ng)]
    return Solid(label, c_angles, g_angles, candela, lumens)


def solid_from_ies(ies_data: Dict, label: str = "IES") -> Solid:
    return Solid(
        label,
        ies_data["h_angles"],
        ies_data["v_angles"],
        ies_data["candela"],
        ies_data.get("total_lumens"),
    )


def _common_grid(a: Solid, b: Solid, n_c: int = 37, n_g: int = 19) -> Dict:
    """Regular sampling grid spanning the intersection of both angular ranges."""
    c_max = min(max(a.c_angles), max(b.c_angles), 360.0)
    g_lo = max(min(a.g_angles), min(b.g_angles))
    g_hi = min(max(a.g_angles), max(b.g_angles))

    c_samples = [round(i * c_max / max(1, n_c - 1), 4) for i in range(n_c)]
    g_samples = [round(g_lo + i * (g_hi - g_lo) / max(1, n_g - 1), 4) for i in range(n_g)]
    return {"c": c_samples, "g": g_samples}


def compare(
    ldt: Ldt,
    reference: Dict,
    reference_kind: str = "ies",
    tolerance_pct: float = 10.0,
    grid_c: int = 37,
    grid_g: int = 19,
) -> Dict:
    """
    Compare `ldt` (the file being checked) against `reference`
    (an ies_reader.read_ies() dict, or another Ldt if reference_kind='ldt').

    Returns a JSON-serialisable compliance report.
    """
    a = solid_from_ldt(ldt, label="Source LDT")
    b = solid_from_ies(reference, label="Reference IES") if reference_kind == "ies" \
        else solid_from_ldt(reference, label="Reference LDT")

    grid = _common_grid(a, b, grid_c, grid_g)

    deltas_pct = []
    deltas_abs = []
    samples = []

    for c in grid["c"]:
        for g in grid["g"]:
            ia = photometry.candela_at(a.c_angles, a.g_angles, a.candela, c, g)
            ib = photometry.candela_at(b.c_angles, b.g_angles, b.candela, c, g)
            diff = ia - ib
            pct = (diff / ib * 100.0) if abs(ib) > 1e-6 else (0.0 if abs(diff) < 1e-6 else 100.0)
            deltas_abs.append(diff)
            deltas_pct.append(pct)
            samples.append({"c": c, "g": g, "source_cd": round(ia, 2),
                             "reference_cd": round(ib, 2), "pct_delta": round(pct, 2)})

    n = len(deltas_pct) or 1
    max_abs_pct = max((abs(p) for p in deltas_pct), default=0.0)
    rms_pct = math.sqrt(sum(p * p for p in deltas_pct) / n)
    mean_pct = sum(deltas_pct) / n
    out_of_tolerance = sum(1 for p in deltas_pct if abs(p) > tolerance_pct)

    flux_a = photometry.zonal_flux(a.c_angles, a.g_angles, a.candela)
    flux_b = photometry.zonal_flux(b.c_angles, b.g_angles, b.candela)
    down_a, up_a = photometry.downward_upward_flux(a.c_angles, a.g_angles, a.candela)
    down_b, up_b = photometry.downward_upward_flux(b.c_angles, b.g_angles, b.candela)

    peak_a = max(v for row in a.candela for v in row)
    peak_b = max(v for row in b.candela for v in row)

    def row_at(solid: Solid, target_c: float) -> List[float]:
        ci, _, _ = photometry._bracket(solid.c_angles, target_c, wrap=360.0)
        return solid.candela[ci]

    beam_a_c0 = photometry.beam_angle_at_c(a.g_angles, row_at(a, 0.0))
    beam_b_c0 = photometry.beam_angle_at_c(b.g_angles, row_at(b, 0.0))
    beam_a_c90 = photometry.beam_angle_at_c(a.g_angles, row_at(a, 90.0))
    beam_b_c90 = photometry.beam_angle_at_c(b.g_angles, row_at(b, 90.0))

    flux_pct_delta = ((flux_a - flux_b) / flux_b * 100.0) if flux_b else 0.0
    peak_pct_delta = ((peak_a - peak_b) / peak_b * 100.0) if peak_b else 0.0

    if max_abs_pct <= tolerance_pct and abs(flux_pct_delta) <= tolerance_pct:
        verdict = "PASS"
    elif max_abs_pct <= tolerance_pct * 2 and abs(flux_pct_delta) <= tolerance_pct * 2:
        verdict = "WARN"
    else:
        verdict = "FAIL"

    return {
        "verdict": verdict,
        "tolerance_pct": tolerance_pct,
        "grid_points_compared": len(samples),
        "points_outside_tolerance": out_of_tolerance,
        "candela_deviation": {
            "max_abs_pct": round(max_abs_pct, 2),
            "rms_pct": round(rms_pct, 2),
            "mean_pct": round(mean_pct, 2),
        },
        "flux": {
            "source_lm": round(flux_a, 1),
            "reference_lm": round(flux_b, 1),
            "pct_delta": round(flux_pct_delta, 2),
            "source_downward_lm": round(down_a, 1),
            "source_upward_lm": round(up_a, 1),
            "reference_downward_lm": round(down_b, 1),
            "reference_upward_lm": round(up_b, 1),
        },
        "peak_intensity_cd": {
            "source": round(peak_a, 1),
            "reference": round(peak_b, 1),
            "pct_delta": round(peak_pct_delta, 2),
        },
        "beam_angle_deg": {
            "source_c0": round(beam_a_c0, 1),
            "reference_c0": round(beam_b_c0, 1),
            "source_c90": round(beam_a_c90, 1),
            "reference_c90": round(beam_b_c90, 1),
        },
        "worst_samples": sorted(samples, key=lambda s: -abs(s["pct_delta"]))[:15],
    }
