"""
ldt_tools.geometry
====================

Shape-agnostic plan geometry: area, perimeter, centroid and cavity-ratio
helpers. Written once so a plain rectangle (today's only room shape) and a
future arbitrary simple polygon (planned) share exactly the same equations
-- nothing about `reflectance.py` or `heatmap.py` needs to know or care
which one it's looking at, because both are reduced to (area, perimeter)
before they're used.

A room's plan is a list of (x, y) vertices in metres, in order (clockwise
or counter-clockwise, consistently) around the perimeter, e.g.:

    rectangle_vertices(L, W) == [(0,0), (L,0), (L,W), (0,W)]
"""

from __future__ import annotations

import math
from typing import List, Tuple

Point = Tuple[float, float]


def rectangle_vertices(length: float, width: float) -> List[Point]:
    """The rectangle special case, expressed as a 4-vertex polygon."""
    return [(0.0, 0.0), (length, 0.0), (length, width), (0.0, width)]


def polygon_area(vertices: List[Point]) -> float:
    """
    Shoelace formula:

        A = 1/2 * | sum_i ( x_i * y_(i+1) - x_(i+1) * y_i ) |

    Works for any simple (non-self-intersecting) polygon, convex or not,
    regardless of vertex winding direction (the abs() takes care of sign).
    For the rectangle special case this reduces to A = length * width.
    """
    n = len(vertices)
    if n < 3:
        return 0.0
    total = 0.0
    for i in range(n):
        x1, y1 = vertices[i]
        x2, y2 = vertices[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def polygon_perimeter(vertices: List[Point]) -> float:
    """
    Pm = sum_i | V_(i+1) - V_i |

    For the rectangle special case this reduces to Pm = 2*(length + width).
    """
    n = len(vertices)
    if n < 2:
        return 0.0
    total = 0.0
    for i in range(n):
        x1, y1 = vertices[i]
        x2, y2 = vertices[(i + 1) % n]
        total += math.hypot(x2 - x1, y2 - y1)
    return total


def polygon_centroid(vertices: List[Point]) -> Point:
    """
    True AREA centroid (not the vertex average -- those differ for any
    non-regular polygon), via the standard shoelace-derived formula:

        Cx = 1/(6A) * sum_i (x_i + x_(i+1)) * (x_i*y_(i+1) - x_(i+1)*y_i)
        Cy = 1/(6A) * sum_i (y_i + y_(i+1)) * (x_i*y_(i+1) - x_(i+1)*y_i)

    Falls back to the plain vertex mean for degenerate (near-zero-area)
    input, e.g. fewer than 3 points or collinear points.
    """
    n = len(vertices)
    if n < 3:
        if not vertices:
            return (0.0, 0.0)
        xs = [v[0] for v in vertices]
        ys = [v[1] for v in vertices]
        return (sum(xs) / len(xs), sum(ys) / len(ys))

    a_signed = 0.0
    cx = 0.0
    cy = 0.0
    for i in range(n):
        x1, y1 = vertices[i]
        x2, y2 = vertices[(i + 1) % n]
        cross = x1 * y2 - x2 * y1
        a_signed += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    a_signed /= 2.0
    if abs(a_signed) < 1e-12:
        xs = [v[0] for v in vertices]
        ys = [v[1] for v in vertices]
        return (sum(xs) / len(xs), sum(ys) / len(ys))
    return (cx / (6.0 * a_signed), cy / (6.0 * a_signed))


def bounding_box(vertices: List[Point]) -> Tuple[float, float, float, float]:
    """(min_x, min_y, max_x, max_y) -- used to bound a calc/fixture grid
    before masking it down to the true polygon with `point_in_polygon`."""
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    return (min(xs), min(ys), max(xs), max(ys))


def point_in_polygon(point: Point, vertices: List[Point]) -> bool:
    """
    Standard ray-casting point-in-polygon test (odd-even rule). For
    future use: masking calculation-grid points and/or fixture positions
    to an arbitrary (non-rectangular) room outline instead of the current
    bounding rectangle.
    """
    x, y = point
    n = len(vertices)
    inside = False
    x1, y1 = vertices[-1]
    for i in range(n):
        x2, y2 = vertices[i]
        if (y1 > y) != (y2 > y):
            x_at_y = (x2 - x1) * (y - y1) / (y2 - y1 + 1e-15) + x1
            if x < x_at_y:
                inside = not inside
        x1, y1 = x2, y2
    return inside


def cavity_ratio(area: float, perimeter: float, height: float) -> float:
    """
    General Cavity Ratio (IES Zonal Cavity Method / Lumen Method), valid
    for ANY plan shape, not only rectangles:

        CR = 2.5 * h * Pm / A

    h = cavity height (m), Pm = plan perimeter (m), A = plan area (m^2).

    For a rectangle L x W this reduces to the familiar textbook form
    (Pm = 2(L+W)):

        CR = 2.5*h*2*(L+W) / (L*W) = 5*h*(L+W) / (L*W)

    which is the formula printed in every lighting-design handbook for
    Room/Ceiling/Floor Cavity Ratio -- confirming this is just the
    general (any-shape) version of the same equation, not a different one.
    """
    if area <= 0:
        return 0.0
    return 2.5 * height * perimeter / area
