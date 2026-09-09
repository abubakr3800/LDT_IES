"""
ldt_tools.photometry
======================

Core, format-agnostic photometric maths shared by the compliance-comparison
and room heat-map features. Operates on a plain "photometric solid":

    c_angles:  [float]        C-plane azimuths, degrees, 0-360, ascending, wraps
    g_angles:  [float]        gamma angles (from nadir), degrees, 0-180, ascending
    candela:   [[float]]      candela[i][j] = intensity at (c_angles[i], g_angles[j])
                               ABSOLUTE candela (already lumen-scaled)

Every equation below is the standard photometric-engineering formula used by
lighting-design software (Dialux/Relux/AGi32-class point-by-point method);
sources: IESNA LM-63 / CIE 121, EN 12464-1 measurement-grid recommendation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Tuple


# ------------------------------------------------------------------
# Candela interpolation
# ------------------------------------------------------------------

def _bracket(sorted_vals: List[float], x: float, wrap: float = None) -> Tuple[int, int, float]:
    """
    Find i0, i1, t such that value = lerp(sorted_vals[i0], sorted_vals[i1], t)
    brackets x. If `wrap` is given (e.g. 360 for C-angles), x and the list
    are treated as circular.
    """
    n = len(sorted_vals)
    if n == 1:
        return 0, 0, 0.0

    if wrap is not None:
        x = x % wrap
        for i in range(n):
            a = sorted_vals[i]
            b = sorted_vals[(i + 1) % n]
            b_eff = b if i + 1 < n else b + wrap
            if a <= x <= b_eff:
                span = b_eff - a
                t = (x - a) / span if span > 0 else 0.0
                return i, (i + 1) % n, t
        return n - 1, 0, 0.0

    if x <= sorted_vals[0]:
        return 0, 0, 0.0
    if x >= sorted_vals[-1]:
        return n - 1, n - 1, 0.0

    for i in range(n - 1):
        a, b = sorted_vals[i], sorted_vals[i + 1]
        if a <= x <= b:
            span = b - a
            t = (x - a) / span if span > 0 else 0.0
            return i, i + 1, t

    return n - 1, n - 1, 0.0


def candela_at(c_angles: List[float], g_angles: List[float], candela: List[List[float]],
                c: float, g: float) -> float:
    """
    Bilinear interpolation of candela at azimuth `c` (deg, wraps at 360) and
    polar angle `g` (deg, 0=nadir/straight down .. 180, clamped).

    I(C,g) ~=  (1-tc)(1-tg) I00 + tc(1-tg) I10 + (1-tc)tg I01 + tc*tg I11
    """
    g = max(g_angles[0], min(g_angles[-1], g))

    ci0, ci1, tc = _bracket(c_angles, c, wrap=360.0)
    gi0, gi1, tg = _bracket(g_angles, g)

    i00 = candela[ci0][gi0]
    i10 = candela[ci1][gi0]
    i01 = candela[ci0][gi1]
    i11 = candela[ci1][gi1]

    return (
        (1 - tc) * (1 - tg) * i00
        + tc * (1 - tg) * i10
        + (1 - tc) * tg * i01
        + tc * tg * i11
    )


# ------------------------------------------------------------------
# Zonal flux integration  (total luminous flux from the candela solid)
# ------------------------------------------------------------------
#
#   Phi = Integral_0^2pi Integral_0^pi  I(C,g) * sin(g) dg dC
#
# Discretised with the midpoint / zonal-constant method: for each gamma
# band [g_j - dg/2, g_j + dg/2] take the C-averaged intensity Ibar(g_j) and
# multiply by the solid angle of that zone (a ring of the sphere):
#
#   zone_solid_angle_j = 2*pi * ( cos(g_j - dg/2) - cos(g_j + dg/2) )
#   Phi ~= sum_j  Ibar(g_j) * zone_solid_angle_j
#
# This is the classic "zonal flux" method used to derive CIE flux codes /
# DFF (downward flux fraction) from a candela table.

def zonal_flux(c_angles: List[float], g_angles: List[float], candela: List[List[float]]) -> float:
    n_c = len(c_angles)
    n_g = len(g_angles)
    if n_c == 0 or n_g == 0:
        return 0.0

    c_bar = [sum(candela[i][j] for i in range(n_c)) / n_c for j in range(n_g)]

    total = 0.0
    for j in range(n_g):
        g = g_angles[j]
        g_prev = g_angles[j - 1] if j > 0 else g
        g_next = g_angles[j + 1] if j < n_g - 1 else g
        lo = g - (g - g_prev) / 2.0
        hi = g + (g_next - g) / 2.0
        lo = max(0.0, lo)
        hi = min(180.0, hi)
        solid_angle = 2 * math.pi * (math.cos(math.radians(lo)) - math.cos(math.radians(hi)))
        total += c_bar[j] * solid_angle

    return total


def downward_upward_flux(c_angles: List[float], g_angles: List[float],
                          candela: List[List[float]]) -> Tuple[float, float]:
    """Split zonal flux into downward (g<=90) and upward (g>90) components."""
    down_g = [g for g in g_angles if g <= 90.0]
    up_g = [g for g in g_angles if g > 90.0]

    def sub_flux(angles):
        if not angles:
            return 0.0
        idx = [g_angles.index(g) for g in angles]
        sub_candela = [[row[i] for i in idx] for row in candela]
        return zonal_flux(c_angles, angles, sub_candela)

    return sub_flux(down_g), sub_flux(up_g)


# ------------------------------------------------------------------
# Beam angle (50% of peak intensity) — useful compliance/summary metric
# ------------------------------------------------------------------

def beam_angle_at_c(g_angles: List[float], intensities_row: List[float]) -> float:
    """
    Full beam angle (deg) at the 50%-of-peak-intensity criterion, for a
    single C-plane's gamma profile. Returns 2 * gamma_50%.
    """
    if not intensities_row:
        return 0.0
    peak = max(intensities_row)
    if peak <= 0:
        return 0.0
    half = peak / 2.0

    peak_idx = intensities_row.index(peak)

    def cross(seq_idx_range):
        for k in seq_idx_range:
            i0, i1 = k
            v0, v1 = intensities_row[i0], intensities_row[i1]
            if (v0 - half) * (v1 - half) <= 0 and v0 != v1:
                t = (half - v0) / (v1 - v0)
                return g_angles[i0] + t * (g_angles[i1] - g_angles[i0])
        return None

    up = cross([(k, k + 1) for k in range(peak_idx, len(intensities_row) - 1)])
    down = cross([(k, k - 1) for k in range(peak_idx, 0, -1)])

    g_low = down if down is not None else g_angles[0]
    g_high = up if up is not None else g_angles[-1]

    return abs(g_high - g_low)
