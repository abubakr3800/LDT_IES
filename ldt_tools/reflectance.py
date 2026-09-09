"""
ldt_tools.reflectance
========================

Room-surface interreflection (the "indirect" / reflected component) via
the classic IES/CIBSE Zonal Cavity Method (a.k.a. the Lumen Method),
reduced to a 3-surface lumped radiosity solve: floor cavity, ceiling
cavity, walls.

This is the piece the original point-by-point calculation (`heatmap.py`)
never had. `illuminance_at_point()` only computes the DIRECT component
(luminaire -> point, straight line, inverse-square x cosine). It has no
notion of the room's walls, ceiling or floor at all, so it could never
reproduce a full lighting-design package's TOTAL illuminance, which
always includes light that has bounced at least once off the room
surfaces.

Reference case this module was calibrated against
----------------------------------------------------
120 x 100 m room, BY101P LED210S/840, 14500 lm/fixture (100 W x 145 lm/W
override), 8.25 x 7.438 m spacing, 7 m mounting height, work plane 0.75 m
(this tool) / 0.80 m (DIALux):

                       this tool, direct only   DIALux (total, incl. reflections)
    E min  (lx)               37.1                        184
    E avg  (lx)               177                          223
    E max  (lx)               206                          255
    Fixtures                  195                           221

E max (dominated by direct light right under a fixture) is by far the
closest of the three; E min (the darkest points, between fixtures / near
walls, where direct light is weakest) is ~5x low. Max-close / min-far-off
is the fingerprint of a missing ambient/interreflected term, not an error
in the direct-component maths -- see `/mnt/user-data/outputs/room-illuminance-model.md`
for the full write-up, including how close this gets on the reference
case and what's still needed to close the remaining gap.

Method
------
Treat the room cavity as 3 large, diffuse (Lambertian), uniformly-lit
surfaces:

    1 = floor cavity opening    (the work plane), reflectance rho_floor
    2 = ceiling cavity opening  (the luminaire/ceiling plane), rho_ceiling
    3 = walls (the cavity's side walls),            rho_walls

Source terms -- the only place the luminaire's actual photometry enters
this model -- are the installation's total downward and upward flux,
already derivable from the .ldt candela solid via
`photometry.downward_upward_flux` (zonal-flux integration, nothing new to
measure):

    Q1_direct = n_fixtures * Phi_down   (lm; hits the floor cavity directly)
    Q2_direct = n_fixtures * Phi_up     (lm; hits the ceiling cavity directly)
    Q3_direct = 0                        (idealisation: no direct flux is
                                           assumed to hit the walls before
                                           crossing the cavity -- the
                                           standard Zonal Cavity Method
                                           assumption)

View (form) factors between the 3 surfaces come from the closed-form
solution for two identical, directly-opposed parallel rectangles
(floor <-> ceiling) -- Hottel; e.g. Incropera "Fundamentals of Heat and
Mass Transfer", Table 13.2, "aligned parallel rectangles":

    F_floor->ceiling = f(L, W, h)                 [form_factor_parallel_rectangles]
    F_floor->wall    = 1 - F_floor->ceiling         (summation rule: floor
                                                      only sees ceiling + walls)
    F_ceiling->floor = F_floor->ceiling             (reciprocity, A_floor=A_ceiling)
    F_ceiling->wall  = 1 - F_floor->ceiling
    F_wall->floor    = A_floor * F_floor->wall / A_wall   (reciprocity)
    F_wall->ceiling  = F_wall->floor                       (symmetry, A_floor=A_ceiling)
    F_wall->wall     = 1 - 2 * F_wall->floor               (summation rule)

Radiosity (exitance) of surface i, J_i = rho_i * E_i (incident flux
density is either absorbed, 1-rho_i, or diffusely re-emitted, rho_i):

    J_i = rho_i * [ Q_i,direct / A_i + sum_j J_j * F_i->j ]

-- a 3x3 linear system, solved directly (`_solve_3x3`, no numpy needed).

The number actually added back onto every point of the existing
direct-only heatmap is the *interreflected* illuminance at the floor
(the direct term is excluded -- the point-by-point pass already supplies
that exactly, to full spatial resolution):

    delta_E_indirect = J_ceiling * F_floor->ceiling + J_wall * F_floor->wall

`delta_E_indirect` is a single room-average number -- this lumped
3-surface model has no spatial resolution within a surface -- added
UNIFORMLY to every grid point:

    E_total(P) = E_direct(P) + delta_E_indirect,   then x maintenance factor

Known limitation
----------------
A spatially-uniform add-on cannot fully reproduce how much MORE the real
(spatially-resolved) interreflected component boosts the darkest points
near walls/between fixtures versus the brightest points right under a
fixture. Closing that last gap needs a full multi-patch radiosity solve
(subdivide floor/ceiling/walls into a grid of patches, not 1 lump each).
Flagged as the next step in the accompanying .md rather than built here,
since the reference case doesn't give us enough independent data points
(DIALux only reports the three headline stats, not a full grid) to
calibrate a finer model against yet.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Tuple

from . import geometry


# ----------------------------------------------------------------------
# DIALux evo "New project" default reflectances == the EN 12464-1 Annex A
# recommended surface-reflectance combination for interior work areas
# (ceiling >= 0.7, walls >= 0.5, floor >= 0.2). This is what a freshly
# created DIALux room starts with unless its surface properties are
# edited -- used here as the default so results are comparable out of the
# box; override with the room's ACTUAL DIALux reflectances (Room
# Properties, or the room's Documentation output) whenever you have them.
# ----------------------------------------------------------------------
DIALUX_DEFAULT_REFLECTANCES = {
    "rho_ceiling": 0.70,
    "rho_walls": 0.50,
    "rho_floor": 0.20,
}


def form_factor_parallel_rectangles(l: float, w: float, h: float) -> float:
    """
    Closed-form view factor between two identical, directly-opposed,
    parallel rectangles (l x w), separated by distance h.

    X = l/h, Y = w/h:

        F = (2 / (pi X Y)) * [
                ln( sqrt( (1+X^2)(1+Y^2) / (1+X^2+Y^2) ) )
                + X*sqrt(1+Y^2)*atan( X / sqrt(1+Y^2) )
                + Y*sqrt(1+X^2)*atan( Y / sqrt(1+X^2) )
                - X*atan(X) - Y*atan(Y)
            ]
    """
    if h <= 0:
        return 1.0  # degenerate zero-height cavity: surfaces coincide
    X = l / h
    Y = w / h
    inside_log = (1 + X ** 2) * (1 + Y ** 2) / (1 + X ** 2 + Y ** 2)
    F = (2.0 / (math.pi * X * Y)) * (
        0.5 * math.log(inside_log)
        + X * math.sqrt(1 + Y ** 2) * math.atan(X / math.sqrt(1 + Y ** 2))
        + Y * math.sqrt(1 + X ** 2) * math.atan(Y / math.sqrt(1 + X ** 2))
        - X * math.atan(X)
        - Y * math.atan(Y)
    )
    return max(0.0, min(1.0, F))


def equivalent_rectangle(area: float, perimeter: float) -> Tuple[float, float]:
    """
    Reduce an arbitrary polygon's (area, perimeter) to an equivalent
    rectangle l x w sharing BOTH the same area and the same perimeter, so
    the parallel-rectangle form-factor formula above stays usable once
    rooms stop being plain rectangles:

        l + w = perimeter / 2
        l * w = area
        =>  t^2 - (perimeter/2)*t + area = 0
            t = [ (P/2) +/- sqrt( (P/2)^2 - 4A ) ] / 2

    If the discriminant is negative (the shape is too "round"/compact for
    any rectangle to share both its area and perimeter -- e.g. close to a
    circle), falls back to a square of the same area (l = w = sqrt(area));
    the interreflection estimate is then an approximation, not exact, for
    that room shape -- flagged by the caller if it matters.
    """
    half_p = perimeter / 2.0
    disc = half_p ** 2 - 4.0 * area
    if disc < 0 or half_p <= 0:
        side = math.sqrt(max(area, 0.0))
        return side, side
    root = math.sqrt(disc)
    l = (half_p + root) / 2.0
    w = (half_p - root) / 2.0
    return max(l, w), max(min(l, w), 1e-6)


@dataclass
class RadiosityResult:
    j_floor: float
    j_ceiling: float
    j_wall: float
    f_floor_ceiling: float
    f_floor_wall: float
    f_wall_floor: float
    e_floor_direct_avg: float
    e_floor_indirect: float
    e_floor_total_avg: float


def _solve_3x3(A, b):
    """Gaussian elimination with partial pivoting for a 3x3 system -- no
    numpy dependency needed for a system this small."""
    n = 3
    M = [list(row) + [b[i]] for i, row in enumerate(A)]
    for col in range(n):
        pivot_row = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[pivot_row][col]) < 1e-14:
            continue
        M[col], M[pivot_row] = M[pivot_row], M[col]
        pivot = M[col][col]
        M[col] = [v / pivot for v in M[col]]
        for r in range(n):
            if r != col:
                factor = M[r][col]
                M[r] = [M[r][k] - factor * M[col][k] for k in range(n + 1)]
    return [M[i][n] for i in range(n)]


def solve_room_radiosity(
    area: float,
    perimeter: float,
    cavity_height: float,
    flux_down_lm: float,
    flux_up_lm: float,
    rho_floor: float,
    rho_ceiling: float,
    rho_walls: float,
) -> RadiosityResult:
    """
    Solve the lumped 3-surface (floor / ceiling / walls) radiosity system
    described in the module docstring; return the average interreflected
    illuminance to add onto the floor / work plane.

    `area`, `perimeter` describe the room-cavity plan (floor == ceiling
    opening, same shape -- use `geometry.polygon_area` /
    `geometry.polygon_perimeter`, rectangle or otherwise).
    `cavity_height` is the luminaire-to-work-plane distance (the "room
    cavity height" -- `heatmap.py`'s existing `height` variable). If the
    true ceiling is higher than the luminaire plane (recessed/suspended
    fixtures with an additional ceiling cavity above them), that extra
    cavity compresses the EFFECTIVE ceiling reflectance below rho_ceiling
    -- not modelled here yet (assumes fixtures flush with/at the ceiling
    plane); see the accompanying .md for the follow-up formula and what
    input (true ceiling height) is needed to add it.
    """
    if area <= 0 or cavity_height <= 0:
        direct_avg = (flux_down_lm / area) if area > 0 else 0.0
        return RadiosityResult(0, 0, 0, 0, 0, 0, direct_avg, 0.0, direct_avg)

    l, w = equivalent_rectangle(area, perimeter)
    f_fc = form_factor_parallel_rectangles(l, w, cavity_height)
    f_fw = 1.0 - f_fc

    a_floor = area
    a_ceiling = area
    a_wall = perimeter * cavity_height

    f_wf = (a_floor * f_fw / a_wall) if a_wall > 0 else 0.0
    f_wc = f_wf
    f_ww = max(0.0, 1.0 - 2.0 * f_wf)

    q_over_a = [flux_down_lm / a_floor, flux_up_lm / a_ceiling, 0.0]
    rho = [rho_floor, rho_ceiling, rho_walls]
    F = [
        [0.0, f_fc, f_fw],    # floor   -> [floor, ceiling, wall]
        [f_fc, 0.0, f_fw],    # ceiling -> [floor, ceiling, wall]
        [f_wf, f_wc, f_ww],   # wall    -> [floor, ceiling, wall]
    ]

    A = [[(1.0 if i == j else 0.0) - rho[i] * F[i][j] for j in range(3)] for i in range(3)]
    b = [rho[i] * q_over_a[i] for i in range(3)]
    j_floor, j_ceiling, j_wall = _solve_3x3(A, b)

    e_floor_direct_avg = q_over_a[0]
    e_floor_indirect = j_ceiling * f_fc + j_wall * f_fw
    e_floor_total_avg = e_floor_direct_avg + e_floor_indirect

    return RadiosityResult(
        j_floor=j_floor, j_ceiling=j_ceiling, j_wall=j_wall,
        f_floor_ceiling=f_fc, f_floor_wall=f_fw, f_wall_floor=f_wf,
        e_floor_direct_avg=e_floor_direct_avg,
        e_floor_indirect=e_floor_indirect,
        e_floor_total_avg=e_floor_total_avg,
    )
