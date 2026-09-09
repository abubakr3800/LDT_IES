"""
ldt_tools.heatmap
====================

Point-by-point illuminance calculation on a room's work plane, for an array
of identical luminaires laid out on a regular spacing grid, driven directly
by a photometric candela solid (from an .ldt or .ies file).

Equations
---------
1. Luminaire array layout (centered grid, given room L x W and spacing
   Sx x Sy):

       nx = max(1, round(L / Sx))
       ny = max(1, round(W / Sy))
       margin_x = (L - (nx - 1) * Sx) / 2
       margin_y = (W - (ny - 1) * Sy) / 2
       fixture positions: x_i = margin_x + i * Sx  (i = 0..nx-1)
                           y_j = margin_y + j * Sy  (j = 0..ny-1)

   (If nx or ny == 1 the single fixture is centered on that axis.)

2. Mounting height above the work plane:

       H = fixture_mounting_height - work_plane_height

   (both measured from the floor; H must be > 0)

3. For a calculation point P=(xp, yp, 0) on the work plane and a luminaire
   at F=(xf, yf, H):

       dx = xp - xf ;  dy = yp - yf
       r  = sqrt(dx^2 + dy^2)                     (horizontal offset)
       d  = sqrt(r^2 + H^2)                       (straight-line distance)
       gamma = atan2(r, H)                         (angle from nadir, deg)
       C = (atan2(dy, dx) - fixture_rotation) mod 360   (azimuth into the
           luminaire's own C-plane frame; C0 is assumed aligned with +X
           room axis unless `fixture_rotation_deg` rotates it)

   Point-by-point illuminance from ONE luminaire (inverse-square law +
   Lambert's cosine law, combined into the single "cos^3" form):

       E_i = I(C, gamma) * cos(gamma) / d^2  =  I(C, gamma) * H / d^3

   Total illuminance at P is the superposition of all luminaires in the
   array (plus an optional light-loss/maintenance factor LLF):

       E(P) = LLF * sum_i E_i(P)

4. Uniformity metrics over the calculation grid (both conventions reported
   since regional practice differs — EN 12464-1 uses U0, some US/IES
   references use Emin/Emax):

       U0 (EN 12464-1 "uniformity")   = Emin / Eavg
       U1 (diversity / Emin:Emax)     = Emin / Emax

5. Default calculation-grid resolution (EN 12464-1 Annex A style spacing
   recommendation, D = longer of L, W):

       p = 0.2 * (1 + log10(D)),  capped at 10 m
       nx_grid = max(1, round(L / p)) ;  ny_grid = max(1, round(W / p))

   Grid points are centered in each cell (same centering rule as the
   fixture layout), i.e. NOT placed on the room's walls.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional

from . import photometry
from . import geometry
from . import reflectance


@dataclass
class Solid:
    c_angles: List[float]
    g_angles: List[float]
    candela: List[List[float]]  # candela[c_idx][g_idx], ABSOLUTE cd


def default_grid_resolution(length: float, width: float) -> float:
    d = max(length, width)
    if d <= 0:
        return 1.0
    p = 0.2 * (1 + math.log10(max(d, 1.0)))
    return min(p, 10.0)


def _centered_positions(span: float, spacing: float) -> List[float]:
    if spacing <= 0 or span <= 0:
        return [span / 2.0]
    n = max(1, round(span / spacing))
    if n == 1:
        return [span / 2.0]
    margin = (span - (n - 1) * spacing) / 2.0
    return [margin + i * spacing for i in range(n)]


def fixture_positions(room_length: float, room_width: float,
                       spacing_x: float, spacing_y: float) -> List[Dict[str, float]]:
    xs = _centered_positions(room_length, spacing_x)
    ys = _centered_positions(room_width, spacing_y)
    return [{"x": round(x, 4), "y": round(y, 4)} for y in ys for x in xs]


def calc_grid_points(room_length: float, room_width: float,
                      resolution: Optional[float] = None) -> Dict:
    p = resolution or default_grid_resolution(room_length, room_width)
    p = max(0.05, p)
    xs = _centered_positions(room_length, p)
    ys = _centered_positions(room_width, p)
    return {"x": xs, "y": ys, "resolution_m": p}


def illuminance_at_point(solid: Solid, fixtures: List[Dict[str, float]],
                          xp: float, yp: float, height: float,
                          fixture_rotation_deg: float = 0.0) -> float:
    total = 0.0
    for f in fixtures:
        dx = xp - f["x"]
        dy = yp - f["y"]
        r = math.hypot(dx, dy)
        d = math.hypot(r, height)
        if d <= 1e-6:
            continue
        gamma = math.degrees(math.atan2(r, height))
        c = (math.degrees(math.atan2(dy, dx)) - fixture_rotation_deg) % 360.0
        I = photometry.candela_at(solid.c_angles, solid.g_angles, solid.candela, c, gamma)
        total += I * height / (d ** 3)
    return total


def compute_heatmap(
    solid: Solid,
    room_length: float,
    room_width: float,
    fixture_height: float,
    work_plane_height: float = 0.75,
    spacing_x: float = 2.0,
    spacing_y: float = 2.0,
    grid_resolution: Optional[float] = None,
    fixture_rotation_deg: float = 0.0,
    maintenance_factor: float = 1.0,
    rho_floor: float = 0.20,
    rho_ceiling: float = 0.70,
    rho_walls: float = 0.50,
    include_interreflection: bool = True,
) -> Dict:
    """
    Point-by-point DIRECT illuminance (unchanged maths) PLUS a room-average
    INDIRECT (interreflected) component from `reflectance.solve_room_radiosity`,
    added uniformly across the grid. Set rho_floor = rho_ceiling = rho_walls = 0
    (or include_interreflection=False) to recover the old direct-only output.

    Defaults (0.20 / 0.70 / 0.50) are the DIALux evo "new project" /
    EN 12464-1-recommended floor / ceiling / wall reflectances -- see
    `reflectance.DIALUX_DEFAULT_REFLECTANCES`. Pass a room's ACTUAL DIALux
    reflectances here once you have them for a closer match.
    """
    height = fixture_height - work_plane_height
    if height <= 0:
        raise ValueError(
            f"Fixture height ({fixture_height} m) must be greater than "
            f"work-plane height ({work_plane_height} m)."
        )

    fixtures = fixture_positions(room_length, room_width, spacing_x, spacing_y)
    grid = calc_grid_points(room_length, room_width, grid_resolution)

    # ---- direct component (exactly as before) ----
    matrix_direct: List[List[float]] = []
    flat_direct: List[float] = []
    for yp in grid["y"]:
        row = []
        for xp in grid["x"]:
            e = illuminance_at_point(solid, fixtures, xp, yp, height, fixture_rotation_deg)
            row.append(e)
            flat_direct.append(e)
        matrix_direct.append(row)

    e_min_direct = min(flat_direct) if flat_direct else 0.0
    e_max_direct = max(flat_direct) if flat_direct else 0.0
    e_avg_direct = (sum(flat_direct) / len(flat_direct)) if flat_direct else 0.0

    # ---- indirect / interreflected component (new) ----
    vertices = geometry.rectangle_vertices(room_length, room_width)
    area = geometry.polygon_area(vertices)
    perimeter = geometry.polygon_perimeter(vertices)
    room_cavity_ratio = geometry.cavity_ratio(area, perimeter, height)

    flux_down_per_fixture, flux_up_per_fixture = photometry.downward_upward_flux(
        solid.c_angles, solid.g_angles, solid.candela
    )
    n_fixtures = len(fixtures)

    radiosity = None
    delta_e_indirect = 0.0
    if include_interreflection and (rho_floor > 0 or rho_ceiling > 0 or rho_walls > 0):
        radiosity = reflectance.solve_room_radiosity(
            area=area,
            perimeter=perimeter,
            cavity_height=height,
            flux_down_lm=flux_down_per_fixture * n_fixtures,
            flux_up_lm=flux_up_per_fixture * n_fixtures,
            rho_floor=rho_floor,
            rho_ceiling=rho_ceiling,
            rho_walls=rho_walls,
        )
        delta_e_indirect = radiosity.e_floor_indirect

    # ---- total = direct(P) + uniform indirect add-on, then LLF ----
    matrix: List[List[float]] = []
    flat: List[float] = []
    for row in matrix_direct:
        new_row = [round((v + delta_e_indirect) * maintenance_factor, 2) for v in row]
        matrix.append(new_row)
        flat.extend(new_row)

    e_min = min(flat) if flat else 0.0
    e_max = max(flat) if flat else 0.0
    e_avg = (sum(flat) / len(flat)) if flat else 0.0

    # "Min/10" and "Max/3" -- robust versions of the true min/max, meant
    # to match what DIALux's Results panel actually labels "Min 1/10" and
    # "Max 1/3" (NOT the literal single lowest/highest grid point): the
    # mean of the lowest 10% and highest ~33% of grid values respectively.
    # This is a best-effort reading of that label, not confirmed against
    # DIALux's own documentation -- see the accompanying .md.
    e_min_p10 = e_max_p33 = None
    if flat:
        ordered = sorted(flat)
        n10 = max(1, round(len(ordered) * 0.10))
        n33 = max(1, round(len(ordered) * (1.0 / 3.0)))
        e_min_p10 = sum(ordered[:n10]) / n10
        e_max_p33 = sum(ordered[-n33:]) / n33

    u0 = (e_min / e_avg) if e_avg > 0 else 0.0
    u1 = (e_min / e_max) if e_max > 0 else 0.0

    result = {
        "matrix": matrix,
        "x": [round(v, 3) for v in grid["x"]],
        "y": [round(v, 3) for v in grid["y"]],
        "grid_resolution_m": round(grid["resolution_m"], 3),
        "fixtures": fixtures,
        "mounting_height_above_workplane_m": round(height, 3),
        "stats": {
            "e_min": round(e_min, 1),
            "e_max": round(e_max, 1),
            "e_avg": round(e_avg, 1),
            "u0_min_over_avg": round(u0, 3),
            "u1_min_over_max": round(u1, 3),
            "num_fixtures": n_fixtures,
            "num_grid_points": len(flat),
            "e_min_p10": round(e_min_p10, 1) if e_min_p10 is not None else None,
            "e_max_p33": round(e_max_p33, 1) if e_max_p33 is not None else None,
        },
    }

    if radiosity is not None:
        result["reflectance"] = {
            "rho_floor": rho_floor,
            "rho_ceiling": rho_ceiling,
            "rho_walls": rho_walls,
            "room_cavity_ratio": round(room_cavity_ratio, 3),
            "flux_down_per_fixture_lm": round(flux_down_per_fixture, 1),
            "flux_up_per_fixture_lm": round(flux_up_per_fixture, 1),
            "j_floor_lx": round(radiosity.j_floor, 2),
            "j_ceiling_lx": round(radiosity.j_ceiling, 2),
            "j_wall_lx": round(radiosity.j_wall, 2),
            "form_factor_floor_ceiling": round(radiosity.f_floor_ceiling, 4),
            "form_factor_floor_wall": round(radiosity.f_floor_wall, 4),
            "delta_e_indirect_lx": round(delta_e_indirect, 2),
            "e_avg_direct_only_lx": round(e_avg_direct, 2),
            "e_min_direct_only_lx": round(e_min_direct, 2),
            "e_max_direct_only_lx": round(e_max_direct, 2),
        }

    return result


def solid_from_ldt(ldt, total_lumens_override: Optional[float] = None) -> Dict:
    """
    Build the absolute-candela Solid used for point-by-point illuminance.

    Header convention (EULUMDAT): `ldt.intensities` is stored per 1000 lm
    ("candela per klm"), so the absolute solid is:

        candela_abs = raw_intensity * (total_lumens / 1000)

    where `total_lumens` normally comes from the header's declared lamp
    flux (see `ies_writer.total_lumens`).

    `total_lumens_override` lets a caller substitute a different total
    luminous flux (e.g. from a user-entered power x efficacy figure)
    without re-deriving anything from the raw per-klm matrix: since

        candela_abs_new = raw_intensity * (total_lumens_new / 1000)
                         = candela_abs_header * (total_lumens_new / total_lumens_header)

    the override is applied as a single ratio (`scale_factor`) against the
    header-scaled solid. Returns a dict with the Solid plus the scaling
    figures so callers/tests can show or assert on them.
    """
    from .ies_writer import total_lumens as ldt_total_lumens

    header_lumens = ldt_total_lumens(ldt)
    scale = header_lumens / 1000.0
    candela_header = [[v * scale for v in row] for row in ldt.intensities]

    if total_lumens_override is not None and header_lumens > 0:
        scale_factor = total_lumens_override / header_lumens
    else:
        scale_factor = 1.0

    candela = [[v * scale_factor for v in row] for row in candela_header]

    c_angles = ldt.header.c_angles or [i * ldt.header.dc for i in range(ldt.header.mc)]
    g_angles = ldt.header.g_angles or [i * ldt.header.dg for i in range(ldt.header.ng)]

    return {
        "solid": Solid(c_angles, g_angles, candela),
        "header_total_lumens": header_lumens,
        "applied_total_lumens": total_lumens_override if total_lumens_override is not None else header_lumens,
        "scale_factor": scale_factor,
    }
