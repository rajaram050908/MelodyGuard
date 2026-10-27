"use strict";

// MelodyGuard API configuration.
const API_BASE = "http://127.0.0.1:8000/api";

const TOKEN_KEY = "melodyguard_admin_token";
const USER_KEY = "melodyguard_admin_user";

const loginView = document.getElementById("login-view");
const dashboardView = document.getElementById("dashboard-view");

const loginForm = document.getElementById("login-form");
const loginButton = document.getElementById("login-button");
const loginMessage = document.getElementById("login-message");

const logoutButton = document.getElementById("logout-button");
const refreshButton = document.getElementById("refresh-button");
const libraryRefreshButton = document.getElementById(
    "library-refresh-button"
);

const dashboardMessage = document.getElementById("dashboard-message");
const libraryMessage = document.getElementById("library-message");

let referenceSongs = [];
let currentLibraryPage = 1;
let libraryPageSize = 20;
let totalLibraryPages = 1;
let totalLibrarySongs = 0;
let librarySearch = "";
let librarySortBy = "title";
let librarySortOrder = "asc";
let currentSection = "overview";



function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, function (char) {
        const entities = {
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
            '"': "&quot;",
            "'": "&#39;"
        };

        return entities[char];
    });
}

// ============================================================
// API REQUEST HELPER
// ============================================================

async function apiRequest(endpoint, options = {}) {
    const token = sessionStorage.getItem(TOKEN_KEY);

    const headers = new Headers(options.headers || {});

    if (token) {
        headers.set("Authorization", `Bearer ${token}`);
    }

    const response = await fetch(`${API_BASE}${endpoint}`, {
        ...options,
        headers
    });

    if (response.status === 401) {
        logout("Your session has expired. Please sign in again.");
        throw new Error("Your session has expired.");
    }

    let data = {};

    try {
        data = await response.json();
    } catch {
        // Handle responses that don't contain JSON.
    }

    if (!response.ok) {
        throw new Error(
            data.detail || `Request failed (${response.status}).`
        );
    }

    return data;
}


// ============================================================
// LOGIN
// ============================================================

loginForm.addEventListener("submit", async (event) => {
    event.preventDefault();

    loginMessage.textContent = "";
    loginMessage.className = "message";

    loginButton.disabled = true;
    loginButton.textContent = "Signing in...";

    const username = document.getElementById("username").value.trim();
    const password = document.getElementById("password").value;

    try {
        const response = await fetch(`${API_BASE}/admin/login`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                username,
                password
            })
        });

        let data = {};

        try {
            data = await response.json();
        } catch {
            // Ignore an empty or invalid response body.
        }

        if (!response.ok) {
            throw new Error(
                data.detail || "Unable to sign in."
            );
        }

        if (!data.access_token) {
            throw new Error("The server did not return an access token.");
        }

        sessionStorage.setItem(TOKEN_KEY, data.access_token);
        sessionStorage.setItem(
            USER_KEY,
            JSON.stringify(data.admin || { username })
        );

        document.getElementById("password").value = "";

        showDashboard();
    } catch (error) {
        loginMessage.textContent = error.message;
        loginMessage.classList.add("error");
    } finally {
        loginButton.disabled = false;
        loginButton.textContent = "Sign In";
    }
});


// ============================================================
// SESSION / LOGOUT
// ============================================================

function showLogin(message = "") {
    loginView.classList.remove("hidden");
    dashboardView.classList.add("hidden");

    loginMessage.textContent = message;
    loginMessage.className = "message";

    if (message) {
        loginMessage.classList.add("error");
    }
}

function logout(message = "") {
    sessionStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(USER_KEY);

    showLogin(message);
}

logoutButton.addEventListener("click", () => {
    logout();
});

async function showDashboard() {
    const userText = sessionStorage.getItem(USER_KEY);
    let user = {};

    try {
        user = JSON.parse(userText || "{}");
    } catch {
        user = {};
    }

    document.getElementById("admin-username").textContent =
        user.username || "Administrator";

    loginView.classList.add("hidden");
    dashboardView.classList.remove("hidden");

    await loadStats();
    await loadSongs();
}


// ============================================================
// DASHBOARD STATISTICS
// ============================================================

function formatNumber(value) {
    return Number(value || 0).toLocaleString();
}

async function loadStats() {
    dashboardMessage.textContent = "Loading database statistics...";
    dashboardMessage.className = "message";

    document.getElementById("api-status").textContent = "Checking...";

    try {
        const data = await apiRequest("/admin/stats");

        document.getElementById("total-songs").textContent =
            formatNumber(data.total_songs);

        document.getElementById("total-segments").textContent =
            formatNumber(data.total_segments);

        document.getElementById("total-searches").textContent =
            formatNumber(data.total_searches);

        document.getElementById("successful-imports").textContent =
            formatNumber(data.successful_imports);

        document.getElementById("failed-imports").textContent =
            formatNumber(data.failed_imports);

        document.getElementById("api-status").textContent =
            "Connected successfully";

        dashboardMessage.textContent = "Statistics updated successfully.";
        dashboardMessage.classList.add("success");
    } catch (error) {
        document.getElementById("api-status").textContent =
            "Connection failed";

        dashboardMessage.textContent = error.message;
        dashboardMessage.classList.add("error");
    }
}

refreshButton.addEventListener("click", loadStats);


// ============================================================
// REFERENCE LIBRARY
// ============================================================

async function loadSongs() {
    const tableBody = document.getElementById("songs-table-body");
    const countLabel = document.getElementById("library-count");

    tableBody.replaceChildren();

    const loadingRow = document.createElement("tr");
    const loadingCell = document.createElement("td");

    loadingCell.colSpan = 5;
    loadingCell.textContent = "Loading reference library...";

    loadingRow.appendChild(loadingCell);
    tableBody.appendChild(loadingRow);

    libraryMessage.textContent = "";
    libraryMessage.className = "message";

    try {
        const params = new URLSearchParams({
            page: String(currentLibraryPage),
            page_size: String(libraryPageSize),
            search: librarySearch,
            sort_by: librarySortBy,
            sort_order: librarySortOrder
        });

        const response = await apiRequest(
            `/admin/songs?${params.toString()}`
        );

        if (
            !response ||
            !Array.isArray(response.items) ||
            typeof response.total !== "number"
        ) {
            throw new Error("Unexpected response format.");
        }

        referenceSongs = response.items;
        currentLibraryPage = response.page;
        totalLibraryPages = Math.max(1, response.total_pages);
        totalLibrarySongs = response.total;
        renderSongs();


        countLabel.textContent = totalLibrarySongs === 0
            ? "No references found"
            : `Showing ${
                (currentLibraryPage - 1) * libraryPageSize + 1
            }–${
                Math.min(
                    currentLibraryPage * libraryPageSize,
                    totalLibrarySongs
                )
            } of ${formatNumber(totalLibrarySongs)} references`;

        renderLibraryPagination();

        libraryMessage.textContent = "Reference library loaded.";
        libraryMessage.classList.add("success");

    } catch (error) {
        tableBody.replaceChildren();

        const row = document.createElement("tr");
        const cell = document.createElement("td");

        cell.colSpan = 5;
        cell.textContent = error.message;

        row.appendChild(cell);
        tableBody.appendChild(row);

        countLabel.textContent = "Unable to load songs";

        libraryMessage.textContent = error.message;
        libraryMessage.classList.add("error");
    }
}


function renderLibraryPagination() {
    let pagination = document.getElementById("library-pagination");

    if (!pagination) {
        pagination = document.createElement("div");
        pagination.id = "library-pagination";
        pagination.className = "library-pagination";

        document.getElementById("library-count")
            .insertAdjacentElement("afterend", pagination);
    }

    pagination.replaceChildren();

    const previousButton = document.createElement("button");
    previousButton.textContent = "← Previous";
    previousButton.className = "secondary-button";
    previousButton.disabled = currentLibraryPage <= 1;

    previousButton.addEventListener("click", () => {
        if (currentLibraryPage > 1) {
            currentLibraryPage--;
            loadSongs();
        }
    });

    const pageLabel = document.createElement("span");
    pageLabel.textContent =
        `Page ${currentLibraryPage} of ${totalLibraryPages}`;

    const nextButton = document.createElement("button");
    nextButton.textContent = "Next →";
    nextButton.className = "secondary-button";
    nextButton.disabled =
        currentLibraryPage >= totalLibraryPages;

    nextButton.addEventListener("click", () => {
        if (currentLibraryPage < totalLibraryPages) {
            currentLibraryPage++;
            loadSongs();
        }
    });

    const pageSizeSelect = document.createElement("select");

    [10, 20, 50, 100].forEach(size => {
        const option = document.createElement("option");

        option.value = String(size);
        option.textContent = `${size} per page`;
        option.selected = size === libraryPageSize;

        pageSizeSelect.appendChild(option);
    });

    pageSizeSelect.addEventListener("change", () => {
        libraryPageSize = Number(pageSizeSelect.value);
        currentLibraryPage = 1;
        loadSongs();
    });

    pagination.append(
        previousButton,
        pageLabel,
        nextButton,
        pageSizeSelect
    );
}

function renderSongs() {
    const tableBody = document.getElementById("songs-table-body");

    if (!tableBody) {
        console.error("Reference library table not found.");
        return;
    }

    tableBody.replaceChildren();

    if (referenceSongs.length === 0) {
        const row = document.createElement("tr");
        const cell = document.createElement("td");

        cell.colSpan = 5;
        cell.textContent = "No matching reference songs found.";

        row.appendChild(cell);
        tableBody.appendChild(row);
        return;
    }

    for (const song of referenceSongs) {
        const row = document.createElement("tr");

        const titleCell = document.createElement("td");
        const artistCell = document.createElement("td");
        const segmentsCell = document.createElement("td");
        const idCell = document.createElement("td");
        const actionsCell = document.createElement("td");

        titleCell.textContent = song.title || "Untitled";
        artistCell.textContent = song.artist || "Unknown artist";

        segmentsCell.textContent = formatNumber(
            song.total_segments ?? song.segment_count ?? 0
        );

        idCell.textContent = String(song.song_id ?? song.id ?? "");

        const deleteButton = document.createElement("button");

        deleteButton.type = "button";
        deleteButton.className = "delete-reference-button";
        deleteButton.textContent = "Delete";
        deleteButton.dataset.songId = String(
            song.song_id ?? song.id ?? ""
        );

        actionsCell.appendChild(deleteButton);

        row.append(
            titleCell,
            artistCell,
            segmentsCell,
            idCell,
            actionsCell
        );

        tableBody.appendChild(row);
    }
}

// Search the entire reference library through the backend.
let librarySearchTimer = null;

document.getElementById("song-search").addEventListener(
    "input",
    (event) => {
        clearTimeout(librarySearchTimer);

        librarySearch = event.target.value.trim();
        currentLibraryPage = 1;

        librarySearchTimer = setTimeout(() => {
            loadSongs();
        }, 300);
    }
);


// Sorting is handled by the backend across all pages.
document.getElementById("song-sort").addEventListener(
    "change",
    (event) => {
        const sortMode = event.target.value;

        const sortOptions = {
            "title-asc": ["title", "asc"],
            "title-desc": ["title", "desc"],
            "artist-asc": ["artist", "asc"],
            "artist-desc": ["artist", "desc"],
            "segments-desc": ["segments", "desc"],
            "segments-asc": ["segments", "asc"],
            "id-desc": ["song_id", "desc"],
            "id-asc": ["song_id", "asc"]
        };

        [librarySortBy, librarySortOrder] =
            sortOptions[sortMode] || ["title", "asc"];

        currentLibraryPage = 1;
        loadSongs();
    }
);


libraryRefreshButton.addEventListener("click", () => {
    currentLibraryPage = 1;
    loadSongs();
});


// ============================================================
// IMPORT HISTORY
// ============================================================

let currentHistoryPage = 1;
let historyPageSize = 20;
let totalHistoryPages = 1;
let totalHistoryItems = 0;

async function loadImportHistory() {
    const tbody = document.getElementById("history-table-body");
    const count = document.getElementById("history-count");
    const message = document.getElementById("history-message");

    if (!tbody) return;

    tbody.replaceChildren();

    const loadingRow = document.createElement("tr");
    const loadingCell = document.createElement("td");

    loadingCell.colSpan = 9;
    loadingCell.textContent = "Loading history...";

    loadingRow.appendChild(loadingCell);
    tbody.appendChild(loadingRow);

    try {
        const search =
            document.getElementById("history-search")?.value || "";

        const activity =
            document.getElementById("history-filter")?.value || "all";

        const params = new URLSearchParams({
            page: String(currentHistoryPage),
            page_size: String(historyPageSize),
            search,
            activity
        });

        const response = await apiRequest(
            `/admin/history?${params.toString()}`
        );

        if (
            !response ||
            !Array.isArray(response.items) ||
            typeof response.total !== "number"
        ) {
            throw new Error("Unexpected history response format.");
        }

        currentHistoryPage = response.page;
        totalHistoryPages = Math.max(1, response.total_pages);
        totalHistoryItems = response.total;

        tbody.replaceChildren();

        if (response.items.length === 0) {
            const row = document.createElement("tr");
            const cell = document.createElement("td");

            cell.colSpan = 9;
            cell.textContent = "No matching activity found.";

            row.appendChild(cell);
            tbody.appendChild(row);
        } else {
            for (const item of response.items) {
                const row = document.createElement("tr");

                const values = [
                    item.created_at
                        ? new Date(item.created_at).toLocaleString()
                        : "—",
                    item.activity === "delete" ? "Deletion" : "Import",
                    item.reference_id ?? "—",
                    item.title || item.filename || "—",
                    item.artist || "—",
                    item.status || "—",
                    String(item.segments ?? 0),
                    item.admin_username || "—",
                    item.details || "—"
                ];

                for (const value of values) {
                    const cell = document.createElement("td");
                    cell.textContent = String(value);
                    row.appendChild(cell);
                }

                tbody.appendChild(row);
            }
        }

        if (count) {
            count.textContent =
                totalHistoryItems === 0
                    ? "No history records"
                    : `Showing ${
                        (currentHistoryPage - 1) * historyPageSize + 1
                    }–${
                        Math.min(
                            currentHistoryPage * historyPageSize,
                            totalHistoryItems
                        )
                    } of ${totalHistoryItems} activities`;
        }

        if (message) {
            message.textContent = "";
            message.className = "message";
        }

        renderHistoryPagination();

    } catch (error) {
        console.error("Failed to load history:", error);

        tbody.replaceChildren();

        const row = document.createElement("tr");
        const cell = document.createElement("td");

        cell.colSpan = 9;
        cell.textContent = error.message;

        row.appendChild(cell);
        tbody.appendChild(row);

        if (count) {
            count.textContent = "Unable to load history";
        }

        if (message) {
            message.textContent = error.message;
            message.className = "message error";
        }
    }
}

function renderHistoryPagination() {
    let container = document.getElementById("history-pagination");

    if (!container) {
        container = document.createElement("div");
        container.id = "history-pagination";
        container.className = "library-pagination";

        document
            .getElementById("history-table-body")
            ?.closest(".table-wrapper")
            ?.insertAdjacentElement("afterend", container);
    }

    container.replaceChildren();

    const previous = document.createElement("button");
    previous.textContent = "← Previous";
    previous.className = "secondary-button";
    previous.disabled = currentHistoryPage <= 1;

    previous.addEventListener("click", () => {
        if (currentHistoryPage > 1) {
            currentHistoryPage--;
            loadImportHistory();
        }
    });

    const label = document.createElement("span");
    label.textContent =
        `Page ${currentHistoryPage} of ${totalHistoryPages}`;

    const next = document.createElement("button");
    next.textContent = "Next →";
    next.className = "secondary-button";
    next.disabled = currentHistoryPage >= totalHistoryPages;

    next.addEventListener("click", () => {
        if (currentHistoryPage < totalHistoryPages) {
            currentHistoryPage++;
            loadImportHistory();
        }
    });

    const pageSize = document.createElement("select");

    [10, 20, 50, 100].forEach(size => {
        const option = document.createElement("option");
        option.value = String(size);
        option.textContent = `${size} per page`;
        option.selected = size === historyPageSize;
        pageSize.appendChild(option);
    });

    pageSize.addEventListener("change", () => {
        historyPageSize = Number(pageSize.value);
        currentHistoryPage = 1;
        loadImportHistory();
    });

    container.append(previous, label, next, pageSize);
}

// ============================================================
// DELETE A REFERENCE
// ============================================================

document.addEventListener("click", async (event) => {
    const button = event.target.closest(".delete-reference-button");
    if (!button) return;

    const songId = button.dataset.songId;
    const song = referenceSongs.find((item) => String(item.song_id) === String(songId));
    const title = song?.title || `song ID ${songId}`;

    const confirmed = window.confirm(
        `Delete "${title}" from the reference library?\n\n` +
        "Its melody segments and saved search-result rows pointing to those segments will also be deleted. This cannot be undone."
    );
    if (!confirmed) return;

    button.disabled = true;
    button.textContent = "Deleting...";
    try {
        const result = await apiRequest(`/admin/references/${encodeURIComponent(songId)}`, {
            method: "DELETE"
        });
        libraryMessage.textContent = result.message || "Reference deleted.";
        libraryMessage.className = "message success";
        await Promise.all([loadSongs(), loadStats()]);
        if (currentSection === "imports") await loadImportHistory();
    } catch (error) {
        libraryMessage.textContent = error.message;
        libraryMessage.className = "message error";
        button.disabled = false;
        button.textContent = "Delete";
    }
});

// ============================================================
// NAVIGATION
// ============================================================

const sectionTitles = {
    overview: "Dashboard Overview",
    library: "Reference Library",
    "add-reference": "Add Reference",
    imports: "Import History"
};

document.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", async () => {
        const section = button.dataset.section;

        if (!sectionTitles[section]) {
            console.error("Unknown dashboard section:", section);
            return;
        }

        currentSection = section;

        // Update active navigation button.
        document.querySelectorAll(".nav-item").forEach((item) => {
            item.classList.toggle("active", item === button);
        });

        // Hide all dashboard sections.
        document.querySelectorAll(".content-section").forEach((element) => {
            element.classList.add("hidden");
        });

        // Show the selected section.
        const targetSection = document.getElementById(
            `${section}-section`
        );

        if (!targetSection) {
            console.error(
                `Dashboard section not found: ${section}-section`
            );
            return;
        }

        targetSection.classList.remove("hidden");

        document.getElementById("page-title").textContent =
            sectionTitles[section];

        // Load data when entering the relevant sections.
        if (section === "overview") {
            await loadStats();
        } else if (section === "library") {
            await loadSongs();
        } else if (section === "imports") {
            await loadImportHistory();
        }
    });
});

// ============================================================
// INITIALIZATION
// ============================================================

async function initialize() {
    const token = sessionStorage.getItem(TOKEN_KEY);

    if (!token) {
        showLogin();
        return;
    }

    await showDashboard();

    document.getElementById("history-search")
    ?.addEventListener("input", () => {
        currentHistoryPage = 1;
        loadImportHistory();
    });

    document.getElementById("history-filter")
        ?.addEventListener("change", () => {
            currentHistoryPage = 1;
            loadImportHistory();
        });

    document.getElementById("history-refresh-button")
        ?.addEventListener("click", () => {
            loadImportHistory();
        });
}

initialize();

// ============================================================
// ADD REFERENCE: METADATA AND UPLOAD
// ============================================================

const midiFilesInput = document.getElementById("midi-files");
const metadataList = document.getElementById("file-metadata-list");
const uploadForm = document.getElementById("reference-upload-form");
const importButton = document.getElementById("import-button");
const uploadMessage = document.getElementById("upload-message");
const importResults = document.getElementById("import-results");

let selectedMidiFiles = [];

function suggestMetadata(filename) {
    // Remove the MIDI extension.
    const nameWithoutExtension = filename.replace(/\.(mid|midi)$/i, "");

    // Interpret "Artist - Song Title.mid" when possible.
    const separatorIndex = nameWithoutExtension.indexOf(" - ");

    if (separatorIndex > 0) {
        return {
            artist: nameWithoutExtension
                .slice(0, separatorIndex)
                .trim(),
            title: nameWithoutExtension
                .slice(separatorIndex + 3)
                .trim()
        };
    }

    // Otherwise use the filename as the title.
    return {
        artist: "",
        title: nameWithoutExtension.trim()
    };
}

function createMetadataCard(file, index) {
    const suggestion = suggestMetadata(file.name);

    const card = document.createElement("div");
    card.className = "metadata-card";

    const heading = document.createElement("h3");
    heading.textContent = `Reference ${index + 1}`;

    const filename = document.createElement("p");
    filename.className = "filename";
    filename.textContent = file.name;

    const fields = document.createElement("div");
    fields.className = "metadata-fields";

    const titleLabel = document.createElement("label");
    titleLabel.textContent = "Song title";

    const titleInput = document.createElement("input");
    titleInput.type = "text";
    titleInput.required = true;
    titleInput.maxLength = 300;
    titleInput.value = suggestion.title;
    titleInput.dataset.metadata = "title";
    titleInput.dataset.index = String(index);

    titleLabel.appendChild(titleInput);

    const artistLabel = document.createElement("label");
    artistLabel.textContent = "Artist";

    const artistInput = document.createElement("input");
    artistInput.type = "text";
    artistInput.maxLength = 300;
    artistInput.value = suggestion.artist;
    artistInput.placeholder = "Unknown artist";
    artistInput.dataset.metadata = "artist";
    artistInput.dataset.index = String(index);

    artistLabel.appendChild(artistInput);

    fields.append(titleLabel, artistLabel);
    card.append(heading, filename, fields);

    return card;
}

midiFilesInput.addEventListener("change", () => {
    selectedMidiFiles = Array.from(midiFilesInput.files || []);

    metadataList.replaceChildren();
    importResults.replaceChildren();

    uploadMessage.textContent = "";
    uploadMessage.className = "message";

    selectedMidiFiles.forEach((file, index) => {
        metadataList.appendChild(createMetadataCard(file, index));
    });

    if (selectedMidiFiles.length === 0) {
        importResults.textContent = "No files selected.";
    }
});

function getMetadataForFile(index) {
    const titleInput = metadataList.querySelector(
        `[data-metadata="title"][data-index="${index}"]`
    );

    const artistInput = metadataList.querySelector(
        `[data-metadata="artist"][data-index="${index}"]`
    );

    return {
        title: titleInput.value.trim(),
        artist: artistInput.value.trim()
    };
}

function displayImportResult(result) {
    const card = document.createElement("div");
    card.className = "import-result-card";

    const details = document.createElement("div");

    const title = document.createElement("h3");
    title.textContent = result.filename;

    const message = document.createElement("p");
    message.textContent = result.message;

    details.append(title, message);

    const status = document.createElement("strong");
    status.className = result.success
        ? "import-status-success"
        : "import-status-failed";

    status.textContent = result.success
        ? "Imported"
        : "Failed";

    card.append(details, status);
    importResults.appendChild(card);
}

uploadForm.addEventListener("submit", async (event) => {
    event.preventDefault();

    if (selectedMidiFiles.length === 0) {
        uploadMessage.textContent = "Select at least one MIDI file.";
        uploadMessage.className = "message error";
        return;
    }

    // Validate all metadata before beginning imports.
    for (let index = 0; index < selectedMidiFiles.length; index++) {
        const metadata = getMetadataForFile(index);

        if (!metadata.title) {
            uploadMessage.textContent =
                `Enter a title for ${selectedMidiFiles[index].name}.`;
            uploadMessage.className = "message error";
            return;
        }
    }

    importButton.disabled = true;
    importButton.textContent = "Importing...";
    midiFilesInput.disabled = true;

    uploadMessage.textContent =
        `Processing ${selectedMidiFiles.length} file(s)...`;

    uploadMessage.className = "message";

    importResults.replaceChildren();

    let succeeded = 0;
    let failed = 0;

    try {
        for (let index = 0; index < selectedMidiFiles.length; index++) {
            const file = selectedMidiFiles[index];
            const metadata = getMetadataForFile(index);

            const formData = new FormData();
            formData.append("file", file);
            formData.append("title", metadata.title);
            formData.append("artist", metadata.artist);

        try {
            const result = await apiRequest(
                "/admin/references",
                {
                    method: "POST",
                    body: formData
                }
            );

            succeeded++;

            displayImportResult({
                filename: file.name,
                success: true,
                message:
                    `${result.title} — ${result.segments_added} segments added.`
            });

        } catch (error) {
            failed++;

            const message = error.message || "Could not import MIDI file.";

            displayImportResult({
                filename: file.name,
                success: false,
                message: message.toLowerCase().includes("duplicate midi file")
                    ? `⚠️ DUPLICATE: ${message}`
                    : message
            });
        }
        }

        uploadMessage.textContent =
            `Finished: ${succeeded} imported, ${failed} failed.`;

        uploadMessage.className =
            failed === 0
                ? "message success"
                : "message error";

        await loadStats();
        await loadSongs();
        await loadImportHistory();
    } finally {
        importButton.disabled = false;
        importButton.textContent = "Import Selected References";
        midiFilesInput.disabled = false;
    }
});

