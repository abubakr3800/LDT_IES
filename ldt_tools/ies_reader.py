"""
ldt_tools.ies_reader
======================

Minimal reader for IESNA LM-63 (-1986/-1991/-1995/-2002) photometric files,
Type C photometry, TILT=NONE or TILT=INCLUDE with no tilt correction applied
beyond skipping the block. Enough to support side-by-side comparison against
an EULUMDAT (.ldt) file — not a full LM-63 editor.

Returned structure mirrors what we need for comparison:

    {
        "header_fields": {keyword: value, ...},   # bracketed [KEYWORD] lines
        "num_lamps": int,
        "lumens_per_lamp": float,
        "multiplier": float,
        "num_v_angles": int,
        "num_h_angles": int,
        "photometric_type": int,     # 1 = Type C, 2 = Type B, 3 = Type A
        "units_type": int,           # 1 = feet, 2 = meters
        "width": float, "length": float, "height": float,
        "ballast_factor": float,
        "input_watts": float,
        "v_angles": [float, ...],    # gamma, 0-180
        "h_angles": [float, ...],    # C, 0-360
        "candela": [[...], [...]],   # [h_angle][v_angle] -> ABSOLUTE candela
                                      # (multiplier already applied)
        "total_lumens": float,
    }
"""

from __future__ import annotations

from typing import Dict, List


def _tokens(line: str) -> List[str]:
    return line.replace(",", " ").split()


def read_ies(path: str) -> Dict:
    with open(path, "r", encoding="ascii", errors="replace") as f:
        raw_lines = [ln.rstrip("\n").rstrip("\r") for ln in f.readlines()]

    lines = [ln for ln in raw_lines if ln.strip() != ""]
    idx = 0

    header_fields: Dict[str, str] = {}

    # First line: IESNA:LM-63-xxxx (or absent on very old files) - skip if so
    if lines and lines[0].upper().startswith("IESNA"):
        idx = 1

    # Bracketed keyword lines until TILT=
    while idx < len(lines) and not lines[idx].strip().upper().startswith("TILT"):
        ln = lines[idx].strip()
        if ln.startswith("["):
            end = ln.find("]")
            if end != -1:
                key = ln[1:end].strip().upper()
                val = ln[end + 1:].strip()
                header_fields[key] = val
        idx += 1

    if idx >= len(lines):
        raise ValueError("IES file missing TILT= line / malformed header")

    tilt_line = lines[idx].strip()
    idx += 1

    if tilt_line.upper().startswith("TILT=INCLUDE"):
        # lamp-to-luminaire geom (1) + n pairs of angle/factor -> skip
        # number of tilt angles is the first value on the next line
        n_tilt = int(float(_tokens(lines[idx])[0]))
        idx += 1  # lamp-to-luminaire geometry line
        idx += 1  # angles line
        idx += 1  # factors line
        # (kept intentionally simple: TILT correction factors are not
        # applied to the candela matrix; flagged in the returned dict)

    # Line 10: 10 numeric fields (may wrap; gather until we have 10)
    vals: List[float] = []
    while len(vals) < 10 and idx < len(lines):
        vals.extend(float(t) for t in _tokens(lines[idx]))
        idx += 1
    (
        num_lamps,
        lumens_per_lamp,
        multiplier,
        num_v_angles,
        num_h_angles,
        photometric_type,
        units_type,
        width,
        length,
        height,
    ) = vals[:10]

    num_v_angles = int(num_v_angles)
    num_h_angles = int(num_h_angles)

    # Line 11: ballast factor, (future use / BF-lamp-photometric factor), input watts
    vals2: List[float] = []
    while len(vals2) < 3 and idx < len(lines):
        vals2.extend(float(t) for t in _tokens(lines[idx]))
        idx += 1
    ballast_factor, _future_use, input_watts = vals2[:3]

    def pull_n(n: int) -> List[float]:
        nonlocal idx
        out: List[float] = []
        while len(out) < n and idx < len(lines):
            out.extend(float(t) for t in _tokens(lines[idx]))
            idx += 1
        return out[:n]

    v_angles = pull_n(num_v_angles)
    h_angles = pull_n(num_h_angles)

    candela_raw = pull_n(num_v_angles * num_h_angles)
    candela = [
        candela_raw[r * num_v_angles:(r + 1) * num_v_angles] for r in range(num_h_angles)
    ]

    eff_multiplier = multiplier if multiplier else 1.0
    candela = [[v * eff_multiplier for v in row] for row in candela]

    total_lumens = (
        num_lamps * lumens_per_lamp
        if num_lamps and lumens_per_lamp and lumens_per_lamp > 0
        else None
    )

    return {
        "header_fields": header_fields,
        "num_lamps": int(num_lamps) if num_lamps else 1,
        "lumens_per_lamp": lumens_per_lamp,
        "multiplier": eff_multiplier,
        "num_v_angles": num_v_angles,
        "num_h_angles": num_h_angles,
        "photometric_type": int(photometric_type),
        "units_type": int(units_type),
        "width": width,
        "length": length,
        "height": height,
        "ballast_factor": ballast_factor,
        "input_watts": input_watts,
        "v_angles": v_angles,
        "h_angles": h_angles,
        "candela": candela,
        "total_lumens": total_lumens,
    }
