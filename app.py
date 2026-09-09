import os
import json
import uuid
import csv
from datetime import datetime, timezone
from dataclasses import is_dataclass, asdict
from pathlib import Path

from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    send_file
)

from pyldt import LdtReader, LdtWriter

from ldt_tools import symmetry, ies_writer, ies_reader, compliance, heatmap


# ============================================================
# APP CONFIG
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

UPLOAD_FOLDER = BASE_DIR / "uploads"

UPLOAD_FOLDER.mkdir(exist_ok=True)

MANIFEST_PATH = UPLOAD_FOLDER / "_manifest.json"

app = Flask(__name__)

app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)

app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024


# ============================================================
# SAVED-FILES MANIFEST
# ============================================================
#
# Every file the user actually uploads through /api/upload is recorded
# here (file_id, original filename, size, upload time). This is what
# powers the "Saved Files" list on the frontend, so a previously
# uploaded .LDT can be reopened (and its polar curve viewed) without
# re-uploading it. Files generated internally (full-beam conversions,
# comparison references, etc.) are NOT added here.

def _load_manifest():
    if not MANIFEST_PATH.exists():
        return []
    try:
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_manifest(entries):
    try:
        with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2)
    except Exception:
        pass


def record_upload(file_id, filename, size, summary=None):
    entries = _load_manifest()

    entries = [e for e in entries if e.get("file_id") != file_id]

    entries.append({
        "file_id": file_id,
        "filename": filename,
        "size": size,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary or {}
    })

    _save_manifest(entries)


def remove_from_manifest(file_id):
    entries = _load_manifest()
    entries = [e for e in entries if e.get("file_id") != file_id]
    _save_manifest(entries)


# ============================================================
# SERIALIZATION
# ============================================================

def to_serializable(obj):
    """
    Convert pyldt dataclasses and objects to JSON-safe data.
    """

    if obj is None:
        return None

    if is_dataclass(obj):
        return to_serializable(asdict(obj))

    if isinstance(obj, dict):
        return {
            str(key): to_serializable(value)
            for key, value in obj.items()
        }

    if isinstance(obj, (list, tuple)):
        return [
            to_serializable(value)
            for value in obj
        ]

    if isinstance(obj, Path):
        return str(obj)

    if isinstance(obj, (str, int, float, bool)):
        return obj

    if hasattr(obj, "__dict__"):
        return {
            key: to_serializable(value)
            for key, value in vars(obj).items()
            if not key.startswith("_")
        }

    return str(obj)


# ============================================================
# SAFE ATTRIBUTE ACCESS
# ============================================================

def get_attribute(obj, *names, default=None):

    for name in names:

        if hasattr(obj, name):
            value = getattr(obj, name)

            if value is not None:
                return value

    return default


# ============================================================
# ANGLE EXTRACTION
# ============================================================

def generate_c_angles(ldt, intensity_rows):

    """
    Generate C-plane angles.

    The expanded intensity matrix normally represents
    all C planes.

    EULUMDAT stores Dc (distance between C-planes) as 0.0 to mean
    "not evenly spaced — read the explicit angle list instead" (the
    list that follows Mc/Dc/Ng/Dg in the file). `pyldt` already parses
    that explicit list into `header.c_angles`, so it must be preferred
    over reconstructing from Dc; blindly computing `i * Dc` when Dc is
    the EULUMDAT "irregular" sentinel (0.0) silently produces an
    all-zero angle list, which is what was collapsing every C-plane
    onto the same polar-chart angle.
    """

    header = ldt.header

    mc = get_attribute(
        header,
        "mc",
        "num_c_planes",
        "number_of_c_planes"
    )

    if mc is None:
        mc = intensity_rows

    try:
        mc = int(mc)
    except Exception:
        mc = intensity_rows

    explicit_angles = get_attribute(
        header,
        "c_angles",
        "c_plane_angles"
    )

    if explicit_angles and len(explicit_angles) == mc:
        try:
            return [round(float(v), 6) for v in explicit_angles]
        except Exception:
            pass

    dc = get_attribute(
        header,
        "dc",
        "c_plane_distance",
        "c_angle_increment"
    )

    if dc:

        try:

            dc = float(dc)

            if dc:
                return [
                    round(i * dc, 6)
                    for i in range(mc)
                ]

        except Exception:
            pass

    # fallback

    if mc <= 1:
        return [0.0]

    step = 360.0 / mc

    return [
        round(i * step, 6)
        for i in range(mc)
    ]


def generate_gamma_angles(ldt, intensity_columns):

    """
    Generate Gamma angles.

    Same EULUMDAT "0.0 means irregular / read the explicit list"
    convention as Dc above applies to Dg — prefer `header.g_angles`
    (already parsed by pyldt) over reconstructing from Dg.
    """

    header = ldt.header

    ng = get_attribute(
        header,
        "ng",
        "num_gamma_angles",
        "number_of_gamma_angles"
    )

    if ng is None:
        ng = intensity_columns

    try:
        ng = int(ng)
    except Exception:
        ng = intensity_columns

    explicit_angles = get_attribute(
        header,
        "g_angles",
        "gamma_angles"
    )

    if explicit_angles and len(explicit_angles) == ng:
        try:
            return [round(float(v), 6) for v in explicit_angles]
        except Exception:
            pass

    dg = get_attribute(
        header,
        "dg",
        "gamma_angle_distance",
        "gamma_angle_increment"
    )

    if dg:

        try:

            dg = float(dg)

            if dg:
                return [
                    round(i * dg, 6)
                    for i in range(ng)
                ]

        except Exception:
            pass

    if ng <= 1:
        return [0.0]

    step = 180.0 / (ng - 1)

    return [
        round(i * step, 6)
        for i in range(ng)
    ]


# ============================================================
# SUMMARY
# ============================================================

def build_summary(ldt, intensities):

    header = ldt.header

    flat_values = []

    for row in intensities:
        for value in row:
            try:
                flat_values.append(float(value))
            except Exception:
                pass

    summary = {

        "luminaire_name":
            get_attribute(
                header,
                "luminaire_name",
                "name"
            ),

        "luminaire_number":
            get_attribute(
                header,
                "luminaire_no",
                "luminaire_number"
            ),

        "manufacturer":
            get_attribute(
                header,
                "company",
                "manufacturer"
            ),

        "number_of_lamps":
            get_attribute(
                header,
                "num_lamps",
                "number_of_lamps"
            ),

        "isym":
            get_attribute(
                header,
                "isym",
                "symmetry"
            ),

        "c_planes":
            len(intensities),

        "gamma_angles":
            len(intensities[0])
            if intensities else 0,

        "minimum_intensity":
            min(flat_values)
            if flat_values else None,

        "maximum_intensity":
            max(flat_values)
            if flat_values else None,

        "average_intensity":
            (
                sum(flat_values) / len(flat_values)
            )
            if flat_values else None,

        "total_samples":
            len(flat_values),

        "isym":
            get_attribute(header, "isym"),

        "is_half_beam":
            symmetry.is_half_beam(ldt),

        "symmetry_label":
            symmetry.detect_symmetry_label(
                get_attribute(header, "isym", default=0)
            ),

        "total_lumens":
            ies_writer.total_lumens(ldt),

        "total_watts":
            ies_writer.total_watts(ldt)

    }

    return summary


# ============================================================
# PARSE LDT
# ============================================================

def parse_ldt(filepath):

    ldt = LdtReader.read(filepath)

    raw_data = to_serializable(ldt)

    intensities = []

    for row in ldt.intensities:

        intensities.append(
            [
                float(value)
                for value in row
            ]
        )

    c_angles = generate_c_angles(
        ldt,
        len(intensities)
    )

    gamma_angles = generate_gamma_angles(
        ldt,
        len(intensities[0])
        if intensities else 0
    )

    summary = build_summary(
        ldt,
        intensities
    )

    return {

        "summary": summary,

        "header":
            to_serializable(ldt.header),

        "c_angles":
            c_angles,

        "gamma_angles":
            gamma_angles,

        "intensities":
            intensities,

        "raw":
            raw_data
    }


# ============================================================
# ROUTES
# ============================================================

@app.route("/")
def index():

    return render_template("index.html")


@app.route("/api/upload", methods=["POST"])
def upload_file():

    if "file" not in request.files:

        return jsonify({
            "success": False,
            "error": "No file uploaded"
        }), 400

    file = request.files["file"]

    if file.filename == "":

        return jsonify({
            "success": False,
            "error": "No file selected"
        }), 400

    if not file.filename.lower().endswith(".ldt"):

        return jsonify({
            "success": False,
            "error": "Only .LDT files are supported"
        }), 400

    unique_name = (
        f"{uuid.uuid4().hex}_"
        f"{Path(file.filename).name}"
    )

    filepath = (
        UPLOAD_FOLDER /
        unique_name
    )

    try:

        file.save(filepath)

        parsed_data = parse_ldt(filepath)

        parsed_data["filename"] = file.filename
        parsed_data["file_id"] = unique_name

        record_upload(
            unique_name,
            file.filename,
            filepath.stat().st_size,
            summary=parsed_data.get("summary")
        )

        return jsonify({
            "success": True,
            "data": parsed_data
        })

    except Exception as error:

        if filepath.exists():
            filepath.unlink()

        return jsonify({
            "success": False,
            "error": str(error)
        }), 500


# ============================================================
# EXPORT JSON
# ============================================================

@app.route("/api/export/json", methods=["POST"])
def export_json():

    data = request.get_json()

    if not data:
        return jsonify({
            "error": "No data received"
        }), 400

    filename = (
        f"ldt_export_"
        f"{uuid.uuid4().hex}.json"
    )

    filepath = UPLOAD_FOLDER / filename

    with open(
        filepath,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            data,
            file,
            indent=2,
            ensure_ascii=False
        )

    return send_file(
        filepath,
        as_attachment=True,
        download_name="photometric_data.json",
        mimetype="application/json"
    )


# ============================================================
# EXPORT CSV
# ============================================================

@app.route("/api/export/csv", methods=["POST"])
def export_csv():

    data = request.get_json()

    if not data:
        return jsonify({
            "error": "No data received"
        }), 400

    filename = (
        f"ldt_matrix_"
        f"{uuid.uuid4().hex}.csv"
    )

    filepath = UPLOAD_FOLDER / filename

    c_angles = data.get(
        "c_angles",
        []
    )

    gamma_angles = data.get(
        "gamma_angles",
        []
    )

    intensities = data.get(
        "intensities",
        []
    )

    with open(
        filepath,
        "w",
        newline="",
        encoding="utf-8"
    ) as file:

        writer = csv.writer(file)

        writer.writerow(
            ["C / Gamma"]
            +
            gamma_angles
        )

        for index, row in enumerate(
            intensities
        ):

            c_angle = (
                c_angles[index]
                if index < len(c_angles)
                else index
            )

            writer.writerow(
                [c_angle]
                +
                row
            )

    return send_file(
        filepath,
        as_attachment=True,
        download_name="intensity_matrix.csv",
        mimetype="text/csv"
    )


# ============================================================
# FILE_ID RESOLUTION (safe lookup of a previously-uploaded LDT)
# ============================================================

def resolve_upload(file_id):
    """
    Resolve a `file_id` (as returned by /api/upload) to a Path inside
    UPLOAD_FOLDER, guarding against path traversal.
    """
    if not file_id:
        return None

    candidate = (UPLOAD_FOLDER / Path(file_id).name).resolve()

    if UPLOAD_FOLDER.resolve() not in candidate.parents:
        return None

    if not candidate.exists():
        return None

    return candidate


# ============================================================
# SAVED FILES: LIST / REOPEN / DELETE
# ============================================================

@app.route("/api/files", methods=["GET"])
def list_files():
    """
    List previously uploaded .LDT files (most recent first) so the
    frontend can offer a "Saved Files" picker — reopen a file, or jump
    straight to its polar curve, without re-uploading it.
    """

    entries = _load_manifest()

    # Drop manifest rows whose underlying file no longer exists on disk.
    valid_entries = [
        e for e in entries
        if resolve_upload(e.get("file_id")) is not None
    ]

    if len(valid_entries) != len(entries):
        _save_manifest(valid_entries)

    valid_entries.sort(
        key=lambda e: e.get("uploaded_at", ""),
        reverse=True
    )

    return jsonify({
        "success": True,
        "files": valid_entries
    })


@app.route("/api/files/<file_id>", methods=["GET"])
def get_saved_file(file_id):
    """
    Re-parse a previously uploaded .LDT (looked up by file_id) and
    return it in the same shape as /api/upload, so the frontend can
    reopen it — including jumping straight to its polar curve — with
    a single click.
    """

    filepath = resolve_upload(file_id)

    if filepath is None:
        return jsonify({
            "success": False,
            "error": "Unknown or missing file_id. It may have been removed."
        }), 404

    try:
        parsed_data = parse_ldt(filepath)

        entries = _load_manifest()
        match = next(
            (e for e in entries if e.get("file_id") == file_id),
            None
        )

        parsed_data["filename"] = (
            match["filename"] if match else filepath.name
        )
        parsed_data["file_id"] = file_id

        return jsonify({
            "success": True,
            "data": parsed_data
        })

    except Exception as error:
        return jsonify({
            "success": False,
            "error": str(error)
        }), 500


@app.route("/api/files/<file_id>", methods=["DELETE"])
def delete_saved_file(file_id):
    """Remove a saved file from disk and from the saved-files list."""

    filepath = resolve_upload(file_id)

    if filepath is None:
        return jsonify({
            "success": False,
            "error": "Unknown or missing file_id."
        }), 404

    try:
        filepath.unlink()
    except Exception as error:
        return jsonify({
            "success": False,
            "error": str(error)
        }), 500

    remove_from_manifest(file_id)

    return jsonify({"success": True})


# ============================================================
# CONVERT: HALF-BEAM -> FULL-BEAM (ISYM normalisation)
# ============================================================

@app.route("/api/convert/full-beam", methods=["POST"])
def convert_full_beam():

    data = request.get_json() or {}

    filepath = resolve_upload(data.get("file_id"))

    if filepath is None:
        return jsonify({
            "success": False,
            "error": "Unknown or missing file_id. Upload the .LDT file first."
        }), 400

    try:
        ldt = LdtReader.read(filepath)

        was_half_beam = symmetry.is_half_beam(ldt)
        original_label = symmetry.detect_symmetry_label(ldt.header.isym)

        full_ldt = symmetry.to_full_beam(ldt)

        out_name = f"full_beam_{uuid.uuid4().hex}.ldt"
        out_path = UPLOAD_FOLDER / out_name

        LdtWriter.write(full_ldt, out_path, overwrite=True)

        parsed = parse_ldt(out_path)
        parsed["filename"] = "full_beam_" + Path(data.get("original_filename", "output.ldt")).name
        parsed["file_id"] = out_name

        return jsonify({
            "success": True,
            "was_half_beam": was_half_beam,
            "original_symmetry": original_label,
            "data": parsed
        })

    except Exception as error:
        return jsonify({
            "success": False,
            "error": str(error)
        }), 500


# ============================================================
# EXPORT: LDT -> IES (Type C)
# ============================================================

@app.route("/api/export/ies", methods=["POST"])
def export_ies():

    data = request.get_json() or {}

    filepath = resolve_upload(data.get("file_id"))

    if filepath is None:
        return jsonify({
            "success": False,
            "error": "Unknown or missing file_id. Upload the .LDT file first."
        }), 400

    try:
        ldt = LdtReader.read(filepath)

        # Always normalise to full-beam (ISYM=0) first so the IES horizontal
        # angle set legitimately spans 0-360 deg, per LM-63 Type C convention.
        full_ldt = symmetry.to_full_beam(ldt)

        out_name = f"ies_export_{uuid.uuid4().hex}.ies"
        out_path = UPLOAD_FOLDER / out_name

        ies_writer.write_ies(
            full_ldt,
            out_path,
            manufacturer=data.get("manufacturer"),
            luminaire_catalog=data.get("luminaire_catalog"),
            test_report=data.get("test_report"),
        )

        download_name = Path(data.get("original_filename", "luminaire.ldt")).stem + ".ies"

        return send_file(
            out_path,
            as_attachment=True,
            download_name=download_name,
            mimetype="text/plain"
        )

    except Exception as error:
        return jsonify({
            "success": False,
            "error": str(error)
        }), 500


# ============================================================
# COMPARE: LDT vs reference (.ies or .ldt) — compliance report
# ============================================================

@app.route("/api/compare", methods=["POST"])
def compare_files():

    file_id = request.form.get("file_id")
    tolerance_pct = float(request.form.get("tolerance_pct", 10.0))

    filepath = resolve_upload(file_id)

    if filepath is None:
        return jsonify({
            "success": False,
            "error": "Unknown or missing file_id for the source .LDT. Upload it first."
        }), 400

    if "reference_file" not in request.files:
        return jsonify({
            "success": False,
            "error": "No reference file uploaded (field name: reference_file)"
        }), 400

    ref_file = request.files["reference_file"]

    if ref_file.filename == "":
        return jsonify({
            "success": False,
            "error": "No reference file selected"
        }), 400

    ref_ext = Path(ref_file.filename).suffix.lower()

    if ref_ext not in (".ies", ".ldt"):
        return jsonify({
            "success": False,
            "error": "Reference file must be .ies or .ldt"
        }), 400

    ref_path = UPLOAD_FOLDER / f"ref_{uuid.uuid4().hex}{ref_ext}"

    try:
        ref_file.save(ref_path)

        ldt = LdtReader.read(filepath)
        full_ldt = symmetry.to_full_beam(ldt)

        if ref_ext == ".ies":
            ref_data = ies_reader.read_ies(ref_path)
            report = compliance.compare(
                full_ldt, ref_data, reference_kind="ies", tolerance_pct=tolerance_pct
            )
        else:
            ref_ldt = symmetry.to_full_beam(LdtReader.read(ref_path))
            report = compliance.compare(
                full_ldt, ref_ldt, reference_kind="ldt", tolerance_pct=tolerance_pct
            )

        return jsonify({
            "success": True,
            "report": report
        })

    except Exception as error:
        return jsonify({
            "success": False,
            "error": str(error)
        }), 500

    finally:
        if ref_path.exists():
            ref_path.unlink()


# ============================================================
# ROOM HEAT MAP: fixture array + point-by-point illuminance + U0/U1
# ============================================================

@app.route("/api/heatmap", methods=["POST"])
def compute_heatmap():

    data = request.get_json() or {}

    filepath = resolve_upload(data.get("file_id"))

    if filepath is None:
        return jsonify({
            "success": False,
            "error": "Unknown or missing file_id. Upload the .LDT file first."
        }), 400

    try:
        room_length = float(data["room_length_m"])
        room_width = float(data["room_width_m"])
        fixture_height = float(data["fixture_height_m"])
        work_plane_height = float(data.get("work_plane_height_m", 0.75))
        spacing_x = float(data.get("spacing_x_m", 2.0))
        spacing_y = float(data.get("spacing_y_m", spacing_x))
        grid_resolution = data.get("grid_resolution_m")
        grid_resolution = float(grid_resolution) if grid_resolution else None
        fixture_rotation_deg = float(data.get("fixture_rotation_deg", 0.0))
        maintenance_factor = float(data.get("maintenance_factor", 1.0))

        # Room-surface reflectances for the interreflected (indirect)
        # component. Defaults are the DIALux evo "new project" / EN 12464-1
        # recommended combination (ceiling 0.70 / walls 0.50 / floor 0.20)
        # -- see ldt_tools.reflectance.DIALUX_DEFAULT_REFLECTANCES. Pass all
        # three as 0, or include_interreflection=false, to get the old
        # direct-only behaviour back.
        rho_floor = float(data.get("rho_floor", 0.20))
        rho_ceiling = float(data.get("rho_ceiling", 0.70))
        rho_walls = float(data.get("rho_walls", 0.50))
        include_interreflection = bool(data.get("include_interreflection", True))

        # Optional override: recompute total lumens from a user-entered
        # power (W) x efficacy (lm/W) instead of trusting the file's
        # declared lamp flux. Both must be supplied together.
        power_w = data.get("power_w")
        efficacy_lm_per_w = data.get("efficacy_lm_per_w")
        total_lumens_override = None
        if power_w not in (None, "") and efficacy_lm_per_w not in (None, ""):
            power_w = float(power_w)
            efficacy_lm_per_w = float(efficacy_lm_per_w)
            if power_w <= 0 or efficacy_lm_per_w <= 0:
                raise ValueError("power_w and efficacy_lm_per_w must both be > 0")
            total_lumens_override = power_w * efficacy_lm_per_w

    except (KeyError, TypeError, ValueError) as error:
        return jsonify({
            "success": False,
            "error": f"Invalid or missing room parameter: {error}"
        }), 400

    try:
        ldt = LdtReader.read(filepath)
        full_ldt = symmetry.to_full_beam(ldt)
        solid_info = heatmap.solid_from_ldt(full_ldt, total_lumens_override=total_lumens_override)

        result = heatmap.compute_heatmap(
            solid_info["solid"],
            room_length=room_length,
            room_width=room_width,
            fixture_height=fixture_height,
            work_plane_height=work_plane_height,
            spacing_x=spacing_x,
            spacing_y=spacing_y,
            grid_resolution=grid_resolution,
            fixture_rotation_deg=fixture_rotation_deg,
            maintenance_factor=maintenance_factor,
            rho_floor=rho_floor,
            rho_ceiling=rho_ceiling,
            rho_walls=rho_walls,
            include_interreflection=include_interreflection,
        )

        result["scaling"] = {
            "header_total_lumens": round(solid_info["header_total_lumens"], 2),
            "applied_total_lumens": round(solid_info["applied_total_lumens"], 2),
            "scale_factor": round(solid_info["scale_factor"], 6),
        }

        return jsonify({
            "success": True,
            "result": result
        })

    except ValueError as error:
        return jsonify({
            "success": False,
            "error": str(error)
        }), 400

    except Exception as error:
        return jsonify({
            "success": False,
            "error": str(error)
        }), 500


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    app.run(
        debug=True,
        host="127.0.0.1",
        port=5000
    )