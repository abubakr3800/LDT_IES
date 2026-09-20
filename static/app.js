let parsedData = null;


// ============================================================
// ELEMENTS
// ============================================================

const fileInput =
    document.getElementById("fileInput");

const dropZone =
    document.getElementById("dropZone");

const loading =
    document.getElementById("loading");

const content =
    document.getElementById("content");

const errorBox =
    document.getElementById("errorBox");

const exportJson =
    document.getElementById("exportJson");

const exportCsv =
    document.getElementById("exportCsv");


// ============================================================
// FILE INPUT
// ============================================================

fileInput.addEventListener(
    "change",
    event => {

        const file =
            event.target.files[0];

        if (file) {

            uploadFile(file);

        }

    }
);


// ============================================================
// DRAG AND DROP
// ============================================================

[
    "dragenter",
    "dragover"
].forEach(
    eventName => {

        dropZone.addEventListener(
            eventName,
            event => {

                event.preventDefault();

                dropZone.classList.add(
                    "dragging"
                );

            }
        );

    }
);


[
    "dragleave",
    "drop"
].forEach(
    eventName => {

        dropZone.addEventListener(
            eventName,
            event => {

                event.preventDefault();

                dropZone.classList.remove(
                    "dragging"
                );

            }
        );

    }
);


dropZone.addEventListener(
    "drop",
    event => {

        const file =
            event.dataTransfer.files[0];

        if (file) {

            uploadFile(file);

        }

    }
);


// ============================================================
// UPLOAD
// ============================================================

async function uploadFile(file) {

    if (
        !file.name
            .toLowerCase()
            .endsWith(".ldt")
    ) {

        showError(
            "Please select a valid .LDT file."
        );

        return;

    }


    hideError();

    loading.classList.remove(
        "hidden"
    );

    content.classList.add(
        "hidden"
    );


    const formData =
        new FormData();

    formData.append(
        "file",
        file
    );


    try {

        const response =
            await fetch(
                "/api/upload",
                {
                    method: "POST",
                    body: formData
                }
            );


        const result =
            await response.json();


        if (
            !result.success
        ) {

            throw new Error(
                result.error ||
                "Failed to parse LDT file"
            );

        }


        parsedData =
            result.data;


        renderApplication();

        loadSavedFiles();


    }
    catch (error) {

        showError(
            error.message
        );

    }
    finally {

        loading.classList.add(
            "hidden"
        );

    }

}


// ============================================================
// SAVED FILES
// ============================================================
//
// Every file uploaded through /api/upload is kept on the server and
// listed here, so any of them can be reopened later — including
// jumping straight to that file's polar curve — without having to
// re-upload it.

const savedFilesPanel =
    document.getElementById("savedFilesPanel");

const savedFilesList =
    document.getElementById("savedFilesList");


async function loadSavedFiles() {

    try {

        const response =
            await fetch("/api/files");

        const result =
            await response.json();

        if (!result.success) {
            return;
        }

        renderSavedFiles(
            result.files || []
        );

    }
    catch (error) {

        // Non-fatal — the saved-files list is a convenience feature,
        // so a network hiccup here shouldn't block the main app.

    }

}


function renderSavedFiles(files) {

    savedFilesList.innerHTML = "";

    if (!files.length) {
        savedFilesPanel.classList.add("hidden");
        return;
    }

    savedFilesPanel.classList.remove("hidden");

    files.forEach(entry => {

        const card =
            document.createElement("div");

        card.className = "saved-file-card";

        if (
            parsedData &&
            parsedData.file_id === entry.file_id
        ) {
            card.classList.add("active");
        }

        const name =
            document.createElement("span");

        name.className = "saved-file-name";
        name.textContent = entry.filename;
        name.title = entry.filename;

        const meta =
            document.createElement("span");

        meta.className = "saved-file-meta";
        meta.textContent = formatSavedFileMeta(entry);

        const actions =
            document.createElement("div");

        actions.className = "saved-file-actions";

        const polarButton =
            document.createElement("button");

        polarButton.type = "button";
        polarButton.className = "sc-action-button secondary saved-file-polar-button";
        polarButton.textContent = "Polar Curve";
        polarButton.title =
            `Open the polar curve for ${entry.filename}`;

        polarButton.addEventListener(
            "click",
            () => openSavedFile(entry.file_id, "polar")
        );

        const openButton =
            document.createElement("button");

        openButton.type = "button";
        openButton.className = "secondary-button saved-file-open-button";
        openButton.textContent = "Open";

        openButton.addEventListener(
            "click",
            () => openSavedFile(entry.file_id, "overview")
        );

        const deleteButton =
            document.createElement("button");

        deleteButton.type = "button";
        deleteButton.className = "saved-file-delete-button";
        deleteButton.textContent = "✕";
        deleteButton.title = "Remove from saved files";

        deleteButton.addEventListener(
            "click",
            event => {
                event.stopPropagation();
                deleteSavedFile(entry.file_id);
            }
        );

        actions.appendChild(openButton);
        actions.appendChild(polarButton);
        actions.appendChild(deleteButton);

        card.appendChild(name);
        card.appendChild(meta);
        card.appendChild(actions);

        savedFilesList.appendChild(card);

    });

}


function formatSavedFileMeta(entry) {

    const summary = entry.summary || {};

    const parts = [];

    if (summary.symmetry_label) {
        parts.push(summary.symmetry_label);
    }

    if (entry.uploaded_at) {

        const date = new Date(entry.uploaded_at);

        if (!isNaN(date)) {
            parts.push(
                date.toLocaleDateString() +
                " " +
                date.toLocaleTimeString(
                    [],
                    { hour: "2-digit", minute: "2-digit" }
                )
            );
        }

    }

    return parts.join(" · ");

}


async function openSavedFile(fileId, initialTab) {

    hideError();

    loading.classList.remove("hidden");
    content.classList.add("hidden");

    try {

        const response =
            await fetch(`/api/files/${fileId}`);

        const result =
            await response.json();

        if (!result.success) {

            throw new Error(
                result.error ||
                "Failed to reopen the saved file"
            );

        }

        parsedData = result.data;

        renderApplication();

        activateTab(initialTab || "overview");

        loadSavedFiles();

        content.scrollIntoView(
            { behavior: "smooth", block: "start" }
        );

    }
    catch (error) {

        showError(error.message);

    }
    finally {

        loading.classList.add("hidden");

    }

}


async function deleteSavedFile(fileId) {

    try {

        await fetch(
            `/api/files/${fileId}`,
            { method: "DELETE" }
        );

    }
    catch (error) {

        // Ignore — the list refresh below will simply still show it
        // if the delete silently failed, and the user can retry.

    }

    loadSavedFiles();

}


loadSavedFiles();


// ============================================================
// RENDER APP
// ============================================================

function renderApplication() {

    document
        .getElementById("filename")
        .textContent =
            parsedData.filename;


    renderSummary();

    renderOverview();

    renderPolarControls();

    renderPolarChart();

    renderHeatmap();

    renderMatrix();

    renderHeader();

    renderJSON();


    content.classList.remove(
        "hidden"
    );


    exportJson.disabled =
        false;

    exportCsv.disabled =
        false;

    if (
        typeof window.scToolsOnData ===
        "function"
    ) {

        window.scToolsOnData();

    }

}


// ============================================================
// SUMMARY
// ============================================================

function renderSummary() {

    const container =
        document.getElementById(
            "summaryCards"
        );

    container.innerHTML =
        "";


    const summary =
        parsedData.summary;


    const fields = [

        [
            "Luminaire",
            summary.luminaire_name
        ],

        [
            "Symmetry (ISYM)",
            summary.isym
        ],

        [
            "C Planes",
            summary.c_planes
        ],

        [
            "Gamma Angles",
            summary.gamma_angles
        ],

        [
            "Max Intensity",
            formatNumber(
                summary.maximum_intensity
            )
        ],

        [
            "Average Intensity",
            formatNumber(
                summary.average_intensity
            )
        ]

    ];


    fields.forEach(
        ([label, value]) => {

            const card =
                document.createElement(
                    "div"
                );

            card.className =
                "summary-card";


            card.innerHTML = `

                <div class="label">
                    ${escapeHTML(label)}
                </div>

                <div class="value">
                    ${escapeHTML(
                        value ?? "-"
                    )}
                </div>

            `;


            container.appendChild(
                card
            );

        }
    );

}


// ============================================================
// OVERVIEW
// ============================================================

function renderOverview() {

    const container =
        document.getElementById(
            "overviewTable"
        );

    container.innerHTML =
        "";


    Object.entries(
        parsedData.summary
    ).forEach(
        ([key, value]) => {

            container.appendChild(
                createProperty(
                    key,
                    value
                )
            );

        }
    );

}


// ============================================================
// HEADER
// ============================================================

function flattenObject(
    object,
    prefix = ""
) {

    const output = [];


    Object.entries(
        object || {}
    ).forEach(
        ([key, value]) => {

            const path =
                prefix
                    ?
                    `${prefix}.${key}`
                    :
                    key;


            if (
                value !== null &&
                typeof value === "object" &&
                !Array.isArray(value)
            ) {

                output.push(
                    ...flattenObject(
                        value,
                        path
                    )
                );

            }
            else {

                output.push(
                    [
                        path,
                        value
                    ]
                );

            }

        }
    );


    return output;

}


function renderHeader() {

    const container =
        document.getElementById(
            "headerContent"
        );

    container.innerHTML =
        "";


    const fields =
        flattenObject(
            parsedData.header
        );


    fields.forEach(
        ([key, value]) => {

            const element =
                createProperty(
                    key,
                    value
                );

            element.dataset.search =
                (
                    key +
                    " " +
                    value
                )
                .toLowerCase();


            container.appendChild(
                element
            );

        }
    );

}


// ============================================================
// PROPERTY ITEM
// ============================================================

function createProperty(
    key,
    value
) {

    const element =
        document.createElement(
            "div"
        );


    element.className =
        "property-item";


    element.innerHTML = `

        <div class="property-key">
            ${escapeHTML(
                String(key)
            )}
        </div>

        <div class="property-value">
            ${escapeHTML(
                formatValue(value)
            )}
        </div>

    `;


    return element;

}


// ============================================================
// POLAR CONTROLS
// ============================================================

let selectedPlanes = [];


function renderPolarControls() {

    const container =
        document.getElementById(
            "polarControls"
        );

    container.innerHTML =
        "";


    const cAngles =
        parsedData.c_angles;


    // Select useful default planes

    const defaults = [];

    if (cAngles.length > 0)
        defaults.push(0);

    if (cAngles.length > 1)
        defaults.push(
            Math.floor(
                cAngles.length / 4
            )
        );

    if (cAngles.length > 2)
        defaults.push(
            Math.floor(
                cAngles.length / 2
            )
        );


    selectedPlanes =
        [
            ...new Set(defaults)
        ];


    cAngles.forEach(
        (angle, index) => {

            const button =
                document.createElement(
                    "button"
                );


            button.className =
                "plane-button";


            if (
                selectedPlanes.includes(
                    index
                )
            ) {

                button.classList.add(
                    "active"
                );

            }


            button.textContent =
                `C ${formatNumber(angle)}°`;


            button.addEventListener(
                "click",
                () => {

                    if (
                        selectedPlanes.includes(
                            index
                        )
                    ) {

                        selectedPlanes =
                            selectedPlanes.filter(
                                item =>
                                    item !== index
                            );

                    }
                    else {

                        selectedPlanes.push(
                            index
                        );

                    }


                    button.classList.toggle(
                        "active"
                    );


                    renderPolarChart();

                }
            );


            container.appendChild(
                button
            );

        }
    );

}


// ============================================================
// POLAR CHART
// ============================================================
//
// A photometric polar (candela) curve is a BILATERAL cut through the
// luminous-intensity solid: gamma (0 = nadir ... up to the max measured
// gamma, e.g. 90 for a cut-off downlight) is drawn on the right side of
// the vertical axis for the selected C-plane, and mirrored onto the left
// side using the plane 180 deg away (C+180). That is what DIALux/manufacturer
// software render as the familiar "leaf/batwing" curve.
//
// The previous implementation fed `theta: gamma` straight into a
// scatterpolar trace, i.e. treated gamma (0-90 deg) as a full 360 deg
// azimuth sweep. That only ever drew a single quarter-circle wedge and,
// since ISYM=1 files only expose one stored C-plane, looked empty/broken.

function mirrorPlaneIndex(index, cAngles, isym) {

    const mc = cAngles.length;

    if (mc <= 1) {
        // Rotationally symmetric (ISYM=1) or a file with only one stored
        // plane: by definition C = C+180 for every azimuth, so the plane
        // mirrors onto itself.
        return index;
    }

    if (isym === 1) {
        return index;
    }

    // General case (ISYM 0/2/3/4): find whichever stored C-plane sits
    // closest (circularly) to the true opposite azimuth C+180. This is
    // exact whenever that plane was actually measured/stored; for
    // compressed (half/quarter-beam) files it is a best-effort nearest
    // match rather than a re-derivation of the ISYM mirror formula.
    const target = (cAngles[index] + 180) % 360;

    let bestIndex = index;
    let bestDist = Infinity;

    cAngles.forEach((c, i) => {
        const diff = Math.abs(c - target) % 360;
        const dist = Math.min(diff, 360 - diff);
        if (dist < bestDist) {
            bestDist = dist;
            bestIndex = i;
        }
    });

    return bestIndex;
}


function renderPolarChart() {

    const gamma =
        parsedData.gamma_angles;

    const intensities =
        parsedData.intensities;

    const cAngles =
        parsedData.c_angles;

    const isym =
        parsedData.summary
            ? parsedData.summary.isym
            : null;


    const traces = [];


    selectedPlanes.forEach(
        index => {

            if (
                !intensities[index]
            ) {

                return;

            }

            const mirrorIndex =
                mirrorPlaneIndex(
                    index,
                    cAngles,
                    isym
                );

            const rightRow =
                intensities[index];

            const leftRow =
                intensities[mirrorIndex];

            const theta = [];
            const r = [];

            // Left half: mirrored plane, gamma descending from max to 0,
            // plotted as (360 - gamma) so it lands on the opposite side
            // of the vertical (0 deg / nadir) axis.
            for (let i = gamma.length - 1; i >= 0; i--) {
                theta.push((360 - gamma[i]) % 360);
                r.push(leftRow[i]);
            }

            // Right half: the selected plane itself, gamma ascending
            // from 0 to max.
            for (let i = 0; i < gamma.length; i++) {
                theta.push(gamma[i]);
                r.push(rightRow[i]);
            }

            const label =
                mirrorIndex === index
                    ? `C ${formatNumber(cAngles[index])}°`
                    : `C ${formatNumber(cAngles[index])}° / C ${formatNumber(cAngles[mirrorIndex])}°`;

            traces.push({

                type:
                    "scatterpolar",

                mode:
                    "lines",

                r: r,

                theta: theta,

                name: label

            });

        }
    );


    const layout = {

        polar: {

            angularaxis: {

                direction:
                    "clockwise",

                rotation:
                    90,

                thetaunit:
                    "degrees"

            }

        },

        showlegend:
            true,

        margin: {

            t: 40,
            b: 40,
            l: 40,
            r: 40

        }

    };


    Plotly.react(
        "polarChart",
        traces,
        layout,
        {
            responsive:
                true
        }
    );

}


// ============================================================
// HEATMAP
// ============================================================

function renderHeatmap() {

    const trace = {

        type:
            "heatmap",

        z:
            parsedData.intensities,

        x:
            parsedData.gamma_angles,

        y:
            parsedData.c_angles,

        hovertemplate:

            "C: %{y}°<br>" +

            "Gamma: %{x}°<br>" +

            "Intensity: %{z}" +

            "<extra></extra>"

    };


    const layout = {

        xaxis: {

            title:
                "Gamma Angle (°)"

        },

        yaxis: {

            title:
                "C Plane (°)"

        },

        margin: {

            t: 30,
            b: 60,
            l: 70,
            r: 30

        }

    };


    Plotly.react(
        "heatmapChart",
        [trace],
        layout,
        {
            responsive:
                true
        }
    );

}


// ============================================================
// MATRIX
// ============================================================

function renderMatrix() {

    const table =
        document.getElementById(
            "matrixTable"
        );


    table.innerHTML =
        "";


    const thead =
        document.createElement(
            "thead"
        );


    const headerRow =
        document.createElement(
            "tr"
        );


    const firstHeader =
        document.createElement(
            "th"
        );

    firstHeader.textContent =
        "C / Gamma";


    headerRow.appendChild(
        firstHeader
    );


    parsedData.gamma_angles.forEach(
        angle => {

            const th =
                document.createElement(
                    "th"
                );

            th.textContent =
                `${formatNumber(angle)}°`;

            headerRow.appendChild(
                th
            );

        }
    );


    thead.appendChild(
        headerRow
    );


    table.appendChild(
        thead
    );


    const tbody =
        document.createElement(
            "tbody"
        );


    parsedData.intensities.forEach(
        (row, rowIndex) => {

            const tr =
                document.createElement(
                    "tr"
                );


            const firstCell =
                document.createElement(
                    "td"
                );


            firstCell.textContent =
                `C ${formatNumber(
                    parsedData.c_angles[
                        rowIndex
                    ]
                )}°`;


            tr.appendChild(
                firstCell
            );


            row.forEach(
                value => {

                    const td =
                        document.createElement(
                            "td"
                        );

                    td.textContent =
                        formatNumber(value);

                    tr.appendChild(
                        td
                    );

                }
            );


            tbody.appendChild(
                tr
            );

        }
    );


    table.appendChild(
        tbody
    );

}


// ============================================================
// JSON
// ============================================================

function renderJSON() {

    document
        .getElementById(
            "jsonContent"
        )
        .textContent =
            JSON.stringify(
                parsedData.raw,
                null,
                2
            );

}


// ============================================================
// SEARCH HEADER
// ============================================================

document
    .getElementById(
        "headerSearch"
    )
    .addEventListener(
        "input",
        event => {

            const query =
                event.target
                    .value
                    .toLowerCase();


            document
                .querySelectorAll(
                    "#headerContent .property-item"
                )
                .forEach(
                    element => {

                        element.style.display =
                            element.dataset
                                .search
                                .includes(
                                    query
                                )
                                ?
                                "flex"
                                :
                                "none";

                    }
                );

        }
    );


// ============================================================
// MATRIX SEARCH
// ============================================================

document
    .getElementById(
        "matrixSearch"
    )
    .addEventListener(
        "input",
        event => {

            const query =
                event.target
                    .value
                    .toLowerCase();


            document
                .querySelectorAll(
                    "#matrixTable tbody tr"
                )
                .forEach(
                    row => {

                        const text =
                            row.textContent
                                .toLowerCase();


                        row.style.display =
                            text.includes(
                                query
                            )
                            ?
                            ""
                            :
                            "none";

                    }
                );

        }
    );


// ============================================================
// TABS
// ============================================================

function activateTab(tab) {

    const button =
        document.querySelector(
            `.tab-button[data-tab="${tab}"]`
        );

    const panel =
        document.getElementById(tab);

    if (!button || !panel) {
        return;
    }

    document
        .querySelectorAll(
            ".tab-button"
        )
        .forEach(
            item =>
                item.classList
                    .remove(
                        "active"
                    )
        );


    document
        .querySelectorAll(
            ".tab-content"
        )
        .forEach(
            item =>
                item.classList
                    .remove(
                        "active"
                    )
        );


    button.classList.add(
        "active"
    );


    panel.classList.add(
        "active"
    );


    if (
        tab === "polar"
    ) {

        renderPolarChart();

    }


    if (
        tab === "heatmap"
    ) {

        renderHeatmap();

    }

}


document
    .querySelectorAll(
        ".tab-button"
    )
    .forEach(
        button => {

            button.addEventListener(
                "click",
                () => {

                    activateTab(
                        button.dataset.tab
                    );

                }
            );

        }
    );


// ============================================================
// EXPORT JSON
// ============================================================

exportJson.addEventListener(
    "click",
    async () => {

        await exportData(
            "/api/export/json",
            parsedData,
            "photometric_data.json"
        );

    }
);


// ============================================================
// EXPORT CSV
// ============================================================

exportCsv.addEventListener(
    "click",
    async () => {

        await exportData(
            "/api/export/csv",
            parsedData,
            "intensity_matrix.csv"
        );

    }
);


// ============================================================
// EXPORT FUNCTION
// ============================================================

async function exportData(
    url,
    data,
    filename
) {

    try {

        const response =
            await fetch(
                url,
                {

                    method:
                        "POST",

                    headers: {

                        "Content-Type":
                            "application/json"

                    },

                    body:
                        JSON.stringify(
                            data
                        )

                }
            );


        const blob =
            await response.blob();


        const downloadUrl =
            URL.createObjectURL(
                blob
            );


        const link =
            document.createElement(
                "a"
            );


        link.href =
            downloadUrl;


        link.download =
            filename;


        link.click();


        URL.revokeObjectURL(
            downloadUrl
        );

    }
    catch (error) {

        showError(
            "Export failed: " +
            error.message
        );

    }

}


// ============================================================
// HELPERS
// ============================================================

function formatNumber(value) {

    if (
        value === null ||
        value === undefined
    ) {

        return "-";

    }


    const number =
        Number(value);


    if (
        Number.isNaN(number)
    ) {

        return String(value);

    }


    return number.toFixed(3);

}


function formatValue(value) {

    if (
        value === null ||
        value === undefined
    ) {

        return "-";

    }


    if (
        typeof value === "object"
    ) {

        return JSON.stringify(
            value
        );

    }


    return String(value);

}


function escapeHTML(text) {

    return String(text)
        .replace(
            /&/g,
            "&amp;"
        )
        .replace(
            /</g,
            "&lt;"
        )
        .replace(
            />/g,
            "&gt;"
        )
        .replace(
            /"/g,
            "&quot;"
        )
        .replace(
            /'/g,
            "&#039;"
        );

}


function showError(message) {

    errorBox.textContent =
        message;

    errorBox.classList.remove(
        "hidden"
    );

}


function hideError() {

    errorBox.classList.add(
        "hidden"
    );

}