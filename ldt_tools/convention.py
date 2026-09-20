"""
ldt_tools.convention
====================

EULUMDAT (CIE 121) and IESNA LM-63 Type C do NOT orient the luminaire the same way
relative to the C-planes:

    EULUMDAT : luminaire LENGTH axis is parallel to the C90-C270 plane
    LM-63    : luminaire LENGTH axis is parallel to the C0-C180  plane

So the same physical fixture needs its C-planes shifted by 90 deg when moving
between the formats:

    IES  C = (LDT C - 90) mod 360      i.e.  I_ies(a) = I_ldt(a + 90)
    LDT  C = (IES C + 90) mod 360

Luminous-opening dimensions then map straight across (length->length,
width->width) because the axes are re-labelled together with the data.
"""

from __future__ import annotations

from typing import List, Sequence


def expand_ies_to_full(h_angles: Sequence[float], candela: List[List[float]]):
    """Expand an IES horizontal-angle set (0 / 0-90 / 0-180 / 0-360) to 0-360."""
    h = list(h_angles)
    last = h[-1]
    if last >= 359.999 or len(h) == 1 and last != 0:
        return h, [list(r) for r in candela]
    if len(h) == 1 or last == 0:                       # axially symmetric
        step = 5.0
        angs = [i * step for i in range(72)]
        return angs, [list(candela[0]) for _ in angs]
    full_angs, full_rows = [], []
    if abs(last - 90.0) < 1e-6:                        # quadrant symmetric
        def src(a):
            a = a % 360.0
            if a > 180.0: a = 360.0 - a
            if a > 90.0:  a = 180.0 - a
            return a
    elif abs(last - 180.0) < 1e-6:                     # symmetric about 0-180 plane
        def src(a):
            a = a % 360.0
            return 360.0 - a if a > 180.0 else a
    else:
        return h, [list(r) for r in candela]
    step = min(b - a for a, b in zip(h, h[1:]))
    n = int(round(360.0 / step))
    for i in range(n):
        a = i * step
        s = src(a)
        j = min(range(len(h)), key=lambda k: abs(h[k] - s))
        full_angs.append(a); full_rows.append(list(candela[j]))
    return full_angs, full_rows


def _row_at(c_angles: List[float], rows: List[List[float]], target: float) -> List[float]:
    """Cyclic linear interpolation of a C-plane at `target` deg (grid spans 0-360)."""
    t = target % 360.0
    n = len(c_angles)
    for i in range(n):
        a0 = c_angles[i]
        a1 = c_angles[i + 1] if i + 1 < n else c_angles[0] + 360.0
        tt = t if t >= a0 else t + 360.0
        if a0 - 1e-9 <= tt <= a1 + 1e-9:
            if a1 - a0 < 1e-9:
                return list(rows[i])
            w = (tt - a0) / (a1 - a0)
            r0, r1 = rows[i], rows[(i + 1) % n]
            return [x0 + (x1 - x0) * w for x0, x1 in zip(r0, r1)]
    return list(rows[0])


def shift_c_planes(c_angles: List[float], rows: List[List[float]], shift_deg: float):
    """Return rows R on the same C grid with R(a) = rows(a + shift_deg)."""
    return [_row_at(c_angles, rows, a + shift_deg) for a in c_angles]
