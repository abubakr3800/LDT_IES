"""
ldt_tools.symmetry
===================

Half-beam -> full-beam (ISYM) normalisation for EULUMDAT files.

Background
----------
EULUMDAT stores an ISYM code (header line 3) that tells a reader how much of
the 0-360 deg C-plane circle was actually measured:

    ISYM = 0   full 360 deg measured, nothing to mirror   ("full beam")
    ISYM = 1   rotationally symmetric (single C-plane repeated 360 deg)
    ISYM = 2   symmetric about the C0-C180 plane  -> only C0..C180 stored ("half beam")
    ISYM = 3   symmetric about the C90-C270 plane -> only C90..C270 stored ("half beam")
    ISYM = 4   quadrant symmetric -> only one quadrant stored ("quarter beam")

`pyldt.LdtReader.read()` already mirrors the stored planes into a full
[mc x ng] matrix in memory (see pyldt/parser.py::_expand_to_full_matrix), so
`ldt.intensities` is numerically already a full 360 deg matrix regardless of
ISYM. What is NOT fixed automatically is the *header metadata*: ISYM/MC/DC/
C-angles still describe the compressed (half/quarter) file, so re-exporting
(IES, a "full" .ldt, a heat-map calculation, a compliance comparison against
a full-beam file) can silently misinterpret the data downstream.

`to_full_beam()` produces a new, header-consistent `Ldt` object with:
    - isym = 0
    - c_angles regenerated as 0, dc, 2*dc, ... 360-dc  (mc entries, full circle)
    - intensities = the already-mirrored [mc x ng] matrix (defensive re-mirror
      is applied in case the caller passed a matrix read with
      expand_symmetry=False)

Equations
---------
Mirroring is a reflection of azimuth angle C about the symmetry axis:

    ISYM = 2 (mirror about C0-C180):   I(360-C, g) = I(C, g)   for 0 < C < 180
    ISYM = 3 (mirror about C90-C270):  I(180-C, g) = I(C, g)   (mod 360)
    ISYM = 4 (mirror about both axes): combination of the two reflections
    ISYM = 1 (rotationally symmetric): I(C, g) = I(0, g)        for all C
"""

from __future__ import annotations

import copy
from typing import List

from pyldt.model import Ldt


def _mirror_isym2(mc: int, ser: List[List[float]]) -> List[List[float]]:
    half = mc // 2 + 1
    full = [None] * mc
    for i in range(half):
        full[i] = list(ser[i])
    for i in range(1, half - 1):
        full[mc - i] = list(full[i])
    return full


def _mirror_isym3(mc: int, ng: int, ser: List[List[float]], c_angles: List[float]) -> List[List[float]]:
    step = 360.0 / mc if mc else 0.0
    full = [None] * mc
    n = mc // 2 + 1
    start = 270.0
    order = [int(round(((start - k * step) % 360.0) / step)) % mc for k in range(n)]
    for si, ci in enumerate(order):
        full[ci] = list(ser[si])
    for k in range(mc):
        if full[k] is None:
            c_k = round(k * step % 360.0, 6)
            c_mirror = round((180.0 - c_k) % 360.0, 6)
            mirror_idx = int(round(c_mirror / step)) % mc
            full[k] = list(full[mirror_idx]) if full[mirror_idx] is not None else [0.0] * ng
    return full


def _mirror_isym4(mc: int, ser: List[List[float]]) -> List[List[float]]:
    q = mc // 4 + 1
    full = [None] * mc
    for i in range(q):
        full[i] = list(ser[i])
    for k in range(1, mc // 4):
        full[q - 1 + k] = list(full[q - 1 - k])
    base = mc // 2
    for k in range(q):
        full[base + k] = list(full[k])
    for k in range(1, mc // 4):
        full[base + q - 1 + k] = list(full[base + q - 1 - k])
    return full


def is_half_beam(ldt: Ldt) -> bool:
    """True if the file's header still declares a non-zero (compressed) ISYM."""
    return int(getattr(ldt.header, "isym", 0)) != 0


def detect_symmetry_label(isym: int) -> str:
    return {
        0: "None (full 0-360 deg measured)",
        1: "Rotationally symmetric (point-symmetric)",
        2: "Symmetric about C0-C180 (half-beam, 0-180 deg stored)",
        3: "Symmetric about C90-C270 (half-beam, 90-270 deg stored)",
        4: "Quadrant symmetric (quarter-beam, one quadrant stored)",
    }.get(int(isym), f"Unknown ISYM={isym}")


def to_full_beam(ldt: Ldt) -> Ldt:
    """
    Return a NEW Ldt whose header/matrix consistently represent a full
    0-360 deg C-plane measurement (ISYM=0), regardless of the symmetry the
    source file declared.

    Safe to call on an already-full-beam file (isym == 0): returns an
    equivalent copy.
    """
    out = copy.deepcopy(ldt)
    h = out.header
    mc, ng = h.mc, h.ng
    isym = int(h.isym)

    matrix = out.intensities

    # Defensive re-mirror: rebuild the full matrix from whichever planes are
    # non-degenerate, so this function is correct even if it is ever called
    # with a matrix that was read with expand_symmetry=False.
    already_full = len(matrix) == mc   # LdtReader.read() already expanded it
    if already_full:
        pass
    elif isym == 1:
        matrix = [list(matrix[0]) for _ in range(mc)]
    elif isym == 2:
        matrix = _mirror_isym2(mc, matrix)
    elif isym == 3:
        matrix = _mirror_isym3(mc, ng, matrix, h.c_angles)
    elif isym == 4:
        matrix = _mirror_isym4(mc, matrix)
    # isym == 0: already full, nothing to do

    out.intensities = matrix
    h.isym = 0
    h.dc = round(360.0 / mc, 6) if mc else h.dc
    h.c_angles = [round(i * h.dc, 6) for i in range(mc)]

    return out
