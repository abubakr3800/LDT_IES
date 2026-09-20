// ============================================================
// SC PHOTOMETRIC TOOLKIT — frontend handlers
// Relies on `parsedData` (global, set by app.js after /api/upload)
// and is notified via window.scToolsOnData() from renderApplication().
// ============================================================

(function () {

    const convertBtn = document.getElementById("scConvertBtn");
    const exportIesBtn = document.getElementById("scExportIesBtn");
    const compareBtn = document.getElementById("scCompareBtn");
    const heatmapBtn = document.getElementById("scHeatmapBtn");
    const symmetryInfo = document.getElementById("scSymmetryInfo");
    const refFileInput = document.getElementById("scRefFile");
    const compareResults = document.getElementById("scCompareResults");
    const roomStats = document.getElementById("scRoomStats");
    const roomChart = document.getElementById("scRoomHeatmapChart");

    function currentFileId() {
        return parsedData && parsedData.file_id;
    }

    function currentFilename() {
        return (parsedData && parsedData.filename) || "luminaire.ldt";
    }

    // ------------------------------------------------------------
    // Called by app.js once a file is successfully parsed
    // ------------------------------------------------------------
    window.scToolsOnData = function () {

        const enabled = !!currentFileId();

        convertBtn.disabled = !enabled;
        exportIesBtn.disabled = !enabled;
        compareBtn.disabled = !enabled;
        heatmapBtn.disabled = !enabled;

        const summary = parsedData.summary || {};

        symmetryInfo.textContent =
            "ISYM = " + summary.isym +
            "  —  " + summary.symmetry_label +
            (summary.is_half_beam
                ? "  (this file is a half/quarter-beam file; conversion recommended before IES export)"
                : "  (already a full 0-360\u00B0 measurement)");

        compareResults.innerHTML = "";
        roomStats.innerHTML = "";
        roomChart.innerHTML = "";
    };

    // ------------------------------------------------------------
    // Helper: download a blob returned by fetch()
    // ------------------------------------------------------------
    async function downloadResponse(response, fallbackName) {
        const disposition = response.headers.get("Content-Disposition") || "";
        const match = disposition.match(/filename="?([^"]+)"?/);
        const filename = match ? match[1] : fallbackName;

        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
    }

    function showFetchError(container, err) {
        container.innerHTML =
            '<div class="error-box">' +
            (err.message || String(err)) +
            "</div>";
    }

    // ------------------------------------------------------------
    // FULL-BEAM CONVERSION
    // ------------------------------------------------------------
    convertBtn.addEventListener("click", async () => {
        const fileId = currentFileId();
        if (!fileId) return;

        convertBtn.disabled = true;
        const originalLabel = convertBtn.textContent;
        convertBtn.textContent = "Converting...";

        try {
            const resp = await fetch("/api/convert/full-beam", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    file_id: fileId,
                    original_filename: currentFilename()
                })
            });

            const result = await resp.json();

            if (!result.success) {
                throw new Error(result.error || "Conversion failed");
            }

            // Offer the converted file's data as JSON download (the raw
            // .LDT text is also retrievable via /api/export path re-use:
            // simplest UX here is a JSON summary + a follow-up IES export).
            const blob = new Blob(
                [JSON.stringify(result.data, null, 2)],
                { type: "application/json" }
            );
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = "full_beam_" + currentFilename().replace(/\.ldt$/i, "") + ".json";
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);

            symmetryInfo.textContent =
                "Converted: " + result.original_symmetry +
                " \u2192 ISYM = 0 (full 0-360\u00B0). " +
                "New file_id: " + result.data.file_id +
                " (use Export IES now to generate the .IES from this normalised file).";

            // Point subsequent operations (IES export, compare, heatmap) at
            // the newly-normalised full-beam file.
            parsedData.file_id = result.data.file_id;

        } catch (err) {
            symmetryInfo.textContent = "Error: " + err.message;
        } finally {
            convertBtn.disabled = false;
            convertBtn.textContent = originalLabel;
        }
    });

    // ------------------------------------------------------------
    // IES EXPORT
    // ------------------------------------------------------------
    function getRotationDeg() {
        const el = document.getElementById("scIesRotation");
        const v = el ? parseFloat(el.value) : NaN;
        return Number.isFinite(v) ? v : 90;
    }

    exportIesBtn.addEventListener("click", async () => {
        const fileId = currentFileId();
        if (!fileId) return;

        exportIesBtn.disabled = true;
        const originalLabel = exportIesBtn.textContent;
        exportIesBtn.textContent = "Exporting...";

        try {
            const resp = await fetch("/api/export/ies", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    file_id: fileId,
                    original_filename: currentFilename(),
                    rotation_deg: getRotationDeg()
                })
            });

            if (!resp.ok) {
                const result = await resp.json().catch(() => ({}));
                throw new Error(result.error || "IES export failed");
            }

            await downloadResponse(resp, "luminaire.ies");

        } catch (err) {
            alert("IES export error: " + err.message);
        } finally {
            exportIesBtn.disabled = false;
            exportIesBtn.textContent = originalLabel;
        }
    });

    // ------------------------------------------------------------
    // COMPARE / COMPLIANCE
    // ------------------------------------------------------------
    compareBtn.addEventListener("click", async () => {
        const fileId = currentFileId();
        if (!fileId) return;

        const refFile = refFileInput.files[0];
        if (!refFile) {
            compareResults.innerHTML =
                '<div class="error-box">Select a reference .ies or .ldt file first.</div>';
            return;
        }

        const tolerance = document.getElementById("scTolerance").value || "10";

        compareBtn.disabled = true;
        const originalLabel = compareBtn.textContent;
        compareBtn.textContent = "Comparing...";
        compareResults.innerHTML = "";

        try {
            const form = new FormData();
            form.append("file_id", fileId);
            form.append("tolerance_pct", tolerance);
            form.append("reference_file", refFile);
            form.append("rotation_deg", getRotationDeg());

            const resp = await fetch("/api/compare", {
                method: "POST",
                body: form
            });

            const result = await resp.json();

            if (!result.success) {
                throw new Error(result.error || "Comparison failed");
            }

            renderCompareReport(result.report);

        } catch (err) {
            showFetchError(compareResults, err);
        } finally {
            compareBtn.disabled = false;
            compareBtn.textContent = originalLabel;
        }
    });

    function badgeClass(verdict) {
        if (verdict === "PASS") return "pass";
        if (verdict === "WARN") return "warn";
        return "fail";
    }

    function renderCompareReport(report) {
        const dev = report.candela_deviation;
        const flux = report.flux;
        const peak = report.peak_intensity_cd;
        const beam = report.beam_angle_deg;

        let html = "";

        html += '<span class="sc-badge ' + badgeClass(report.verdict) + '">' +
            report.verdict + "</span>";
        html += ' <span class="sc-note">tolerance \u00B1' + report.tolerance_pct +
            "%, " + report.grid_points_compared + " grid points compared, " +
            report.points_outside_tolerance + " outside tolerance</span>";

        html += '<div class="sc-stat-grid">';
        html += statCard("Max candela dev.", dev.max_abs_pct + "%");
        html += statCard("RMS candela dev.", dev.rms_pct + "%");
        html += statCard("Mean candela dev.", dev.mean_pct + "%");
        html += statCard("Flux \u0394", flux.pct_delta + "%");
        html += statCard("Peak cd \u0394", peak.pct_delta + "%");
        html += statCard("Source flux", flux.source_lm + " lm");
        html += statCard("Reference flux", flux.reference_lm + " lm");
        html += statCard("Beam \u00D8 C0 (src/ref)",
            beam.source_c0 + "\u00B0 / " + beam.reference_c0 + "\u00B0");
        html += statCard("Beam \u00D8 C90 (src/ref)",
            beam.source_c90 + "\u00B0 / " + beam.reference_c90 + "\u00B0");
        html += "</div>";

        html += "<h3 class=\"sc-headline\">Worst deviating sample points</h3>";
        html += '<table class="sc-table"><thead><tr>' +
            "<th>C (\u00B0)</th><th>\u03B3 (\u00B0)</th><th>Source cd</th>" +
            "<th>Reference cd</th><th>% \u0394</th></tr></thead><tbody>";

        report.worst_samples.forEach(s => {
            html += "<tr><td>" + s.c + "</td><td>" + s.g + "</td><td>" +
                s.source_cd + "</td><td>" + s.reference_cd + "</td><td>" +
                s.pct_delta + "</td></tr>";
        });

        html += "</tbody></table>";

        compareResults.innerHTML = html;
    }

    function statCard(label, value) {
        return '<div class="sc-stat-card">' +
            '<div class="sc-stat-label">' + label + "</div>" +
            '<div class="sc-stat-value">' + value + "</div>" +
            "</div>";
    }

    // ------------------------------------------------------------
    // ROOM HEATMAP
    // ------------------------------------------------------------
    heatmapBtn.addEventListener("click", async () => {
        const fileId = currentFileId();
        if (!fileId) return;

        heatmapBtn.disabled = true;
        const originalLabel = heatmapBtn.textContent;
        heatmapBtn.textContent = "Calculating...";
        roomStats.innerHTML = "";
        roomChart.innerHTML = "";

        const gridResVal = document.getElementById("scGridRes").value;

        const payload = {
            file_id: fileId,
            room_length_m: parseFloat(document.getElementById("scRoomLength").value),
            room_width_m: parseFloat(document.getElementById("scRoomWidth").value),
            fixture_height_m: parseFloat(document.getElementById("scFixtureHeight").value),
            work_plane_height_m: parseFloat(document.getElementById("scWorkPlaneHeight").value),
            spacing_x_m: parseFloat(document.getElementById("scSpacingX").value),
            spacing_y_m: parseFloat(document.getElementById("scSpacingY").value),
            maintenance_factor: parseFloat(document.getElementById("scMaintFactor").value || "1"),
            rho_ceiling: parseFloat(document.getElementById("scRhoCeiling").value || "0.70"),
            rho_walls: parseFloat(document.getElementById("scRhoWalls").value || "0.50"),
            rho_floor: parseFloat(document.getElementById("scRhoFloor").value || "0.20")
        };

        if (gridResVal) {
            payload.grid_resolution_m = parseFloat(gridResVal);
        }

        const overridePowerVal = document.getElementById("scOverridePower").value;
        const overrideEfficacyVal = document.getElementById("scOverrideEfficacy").value;

        if (overridePowerVal !== "" && overrideEfficacyVal !== "") {
            payload.power_w = parseFloat(overridePowerVal);
            payload.efficacy_lm_per_w = parseFloat(overrideEfficacyVal);
        }

        try {
            const resp = await fetch("/api/heatmap", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });

            const result = await resp.json();

            if (!result.success) {
                throw new Error(result.error || "Heatmap calculation failed");
            }

            renderRoomHeatmap(result.result);

        } catch (err) {
            roomStats.innerHTML =
                '<div class="error-box">' + err.message + "</div>";
        } finally {
            heatmapBtn.disabled = false;
            heatmapBtn.textContent = originalLabel;
        }
    });

    function renderRoomHeatmap(result) {
        const s = result.stats;
        const scaling = result.scaling;

        let html = "";
        html += statCard("E min", s.e_min + " lx");
        html += statCard("E avg", s.e_avg + " lx");
        html += statCard("E max", s.e_max + " lx");
        html += statCard("U0 (Emin/Eavg)", s.u0_min_over_avg);
        html += statCard("U1 (Emin/Emax)", s.u1_min_over_max);
        if (s.e_min_p10 !== null && s.e_min_p10 !== undefined) {
            html += statCard("E min (1/10, DIALux-style)", s.e_min_p10 + " lx");
        }
        if (s.e_max_p33 !== null && s.e_max_p33 !== undefined) {
            html += statCard("E max (1/3, DIALux-style)", s.e_max_p33 + " lx");
        }
        html += statCard("Fixtures", s.num_fixtures);
        html += statCard("Grid points", s.num_grid_points + " (res " +
            result.grid_resolution_m + " m)");
        html += statCard("Mounting height above plane", result.mounting_height_above_workplane_m + " m");

        if (scaling) {
            html += statCard("File lumens", scaling.header_total_lumens + " lm");
            html += statCard("Applied lumens", scaling.applied_total_lumens + " lm");
            html += statCard("Scale factor", scaling.scale_factor + "\u00D7");
        }

        const refl = result.reflectance;
        if (refl) {
            html += statCard("Direct-only E avg", refl.e_avg_direct_only_lx + " lx");
            html += statCard("Indirect add-on (\u0394E)", refl.delta_e_indirect_lx + " lx");
            html += statCard("Room cavity ratio", refl.room_cavity_ratio);
            html += statCard("\u03C1 ceiling / walls / floor",
                refl.rho_ceiling + " / " + refl.rho_walls + " / " + refl.rho_floor);
        }

        roomStats.innerHTML = html;

        const heatTrace = {
            z: result.matrix,
            x: result.x,
            y: result.y,
            type: "heatmap",
            colorscale: [
                [0, "#000000"],
                [0.5, "#a40e16"],
                [1, "#eb1b26"]
            ],
            colorbar: { title: "lux" }
        };

        const fixtureTrace = {
            x: result.fixtures.map(f => f.x),
            y: result.fixtures.map(f => f.y),
            mode: "markers",
            type: "scatter",
            marker: { color: "#ffffff", size: 10, symbol: "x", line: { color: "#000", width: 1 } },
            name: "Fixtures"
        };

        Plotly.newPlot(
            roomChart,
            [heatTrace, fixtureTrace],
            {
                title: "Work-plane illuminance (lux)",
                xaxis: { title: "Room length (m)", scaleanchor: "y" },
                yaxis: { title: "Room width (m)" },
                font: { family: "Poppins, Arial, sans-serif" }
            },
            { responsive: true }
        );
    }

})();
