const API_BASE = "http://127.0.0.1:8000";

const uploadForm = document.getElementById("upload-form");
const fileInput = document.getElementById("midi-file");
const dropZone = document.getElementById("drop-zone");
const uploadButton = document.getElementById("upload-button");
const uploadTitle = document.getElementById("upload-title");
const uploadDescription = document.getElementById("upload-description");
const selectedFile = document.getElementById("selected-file");
const message = document.getElementById("message");
const songsTable = document.getElementById("songs-table");

const resultsSection = document.getElementById("results-section");
const resultsSummary = document.getElementById("results-summary");
const resultsMetadata = document.getElementById("results-metadata");
const resultsEmpty = document.getElementById("results-empty");
const resultsTableWrapper = document.getElementById("results-table-wrapper");
const resultsTable = document.getElementById("results-table");

let selectedMidiFile = null;
let songsCurrentPage = 1;
let songsPageSize = 20;
let songsTotalPages = 1;


// ============================================================
// GENERAL HELPERS
// ============================================================

function showMessage(text, type = "success") {
    message.textContent = text;
    message.className = `message ${type}`;
}

function clearMessage() {
    message.textContent = "";
    message.className = "message hidden";
}

function formatNumber(value) {
    return Number(value || 0).toLocaleString();
}

function formatTime(value) {
    const seconds = Number(value);

    if (!Number.isFinite(seconds)) {
        return "—";
    }

    return `${seconds.toFixed(2)} s`;
}

function formatScore(value) {
    const score = Number(value);

    if (!Number.isFinite(score)) {
        return "—";
    }

    return `${score.toFixed(2)}%`;
}


// ============================================================
// FILE SELECTION
// ============================================================

function setSelectedFile(file) {
    clearMessage();

    if (!file) {
        selectedMidiFile = null;
        selectedFile.classList.add("hidden");
        return;
    }

    const filename = file.name.toLowerCase();

    if (!filename.endsWith(".mid") && !filename.endsWith(".midi")) {
        showMessage("Please select a .mid or .midi file.", "error");
        fileInput.value = "";
        selectedMidiFile = null;
        selectedFile.classList.add("hidden");
        return;
    }

    if (file.size > 20 * 1024 * 1024) {
        showMessage("The maximum file size is 20 MB.", "error");
        fileInput.value = "";
        selectedMidiFile = null;
        selectedFile.classList.add("hidden");
        return;
    }

    selectedMidiFile = file;

    uploadTitle.textContent = file.name;
    uploadDescription.textContent = "Your MIDI file is ready to upload.";

    selectedFile.textContent =
        `${file.name} · ${(file.size / 1024).toFixed(1)} KB`;

    selectedFile.classList.remove("hidden");
}

fileInput.addEventListener("change", () => {
    setSelectedFile(fileInput.files[0]);
});

dropZone.addEventListener("dragover", (event) => {
    event.preventDefault();
    dropZone.classList.add("dragging");
});

dropZone.addEventListener("dragleave", () => {
    dropZone.classList.remove("dragging");
});

dropZone.addEventListener("drop", (event) => {
    event.preventDefault();
    dropZone.classList.remove("dragging");

    const file = event.dataTransfer.files[0];

    if (file) {
        setSelectedFile(file);
    }
});


// ============================================================
// DATABASE HEALTH CHECK
// ============================================================

async function checkHealth() {
    const statusElement = document.getElementById("database-status");

    try {
        const response = await fetch(`${API_BASE}/api/health`);

        if (!response.ok) {
            throw new Error("Backend unavailable");
        }

        const data = await response.json();

        statusElement.textContent =
            data.database === "connected" ? "Connected" : "Unavailable";

    } catch (error) {
        statusElement.textContent = "Offline";
    }
}


// ============================================================
// LOAD REFERENCE SONGS
// ============================================================

async function loadSongs() {
    songsTable.innerHTML = `
        <tr>
            <td colspan="4" class="empty-state">
                Loading catalogue...
            </td>
        </tr>
    `;

    const pageInfo = document.getElementById("songs-page-info");
    const pageNumber = document.getElementById("songs-page-number");
    const previousButton = document.getElementById("songs-prev");
    const nextButton = document.getElementById("songs-next");

    previousButton.disabled = true;
    nextButton.disabled = true;

    try {
        const params = new URLSearchParams({
            page: String(songsCurrentPage),
            page_size: String(songsPageSize)
        });

        const response = await fetch(
            `${API_BASE}/api/songs?${params.toString()}`
        );

        if (!response.ok) {
            throw new Error("Could not load songs.");
        }

        const data = await response.json();

        // Overall catalogue statistics, not just this page.
        document.getElementById("song-count").textContent =
            formatNumber(data.total);

        document.getElementById("segment-count").textContent =
            formatNumber(data.total_segments);

        const songs = data.items;

        songsCurrentPage = data.page;
        songsPageSize = data.page_size;
        songsTotalPages = data.total_pages;

        // Update pagination labels.
        pageNumber.textContent =
            `Page ${songsCurrentPage} of ${songsTotalPages}`;

        const firstItem = data.total === 0
            ? 0
            : (songsCurrentPage - 1) * songsPageSize + 1;

        const lastItem = Math.min(
            songsCurrentPage * songsPageSize,
            data.total
        );

        pageInfo.textContent =
            `Showing ${formatNumber(firstItem)}–${formatNumber(lastItem)} of ${formatNumber(data.total)} songs`;

        previousButton.disabled = songsCurrentPage <= 1;
        nextButton.disabled =
            songsCurrentPage >= songsTotalPages;

        if (songs.length === 0) {
            songsTable.innerHTML = `
                <tr>
                    <td colspan="4" class="empty-state">
                        No reference songs have been added yet.
                    </td>
                </tr>
            `;
            return;
        }

        // Render only the songs returned for this page.
        songsTable.innerHTML = "";

        for (const song of songs) {
            const row = document.createElement("tr");

            const titleCell = document.createElement("td");
            titleCell.className = "song-title";
            titleCell.textContent = song.title;

            const artistCell = document.createElement("td");
            artistCell.className = "artist-name";
            artistCell.textContent =
                song.artist || "Unknown artist";

            const countCell = document.createElement("td");

            const countBadge = document.createElement("span");
            countBadge.className = "count-badge";
            countBadge.textContent =
                formatNumber(song.total_segments);

            countCell.appendChild(countBadge);

            const statusCell = document.createElement("td");
            statusCell.className = "catalogue-status";
            statusCell.textContent = "● Available";

            row.append(
                titleCell,
                artistCell,
                countCell,
                statusCell
            );

            songsTable.appendChild(row);
        }

    } catch (error) {
        console.error("Catalogue loading failed:", error);

        songsTable.innerHTML = `
            <tr>
                <td colspan="4" class="empty-state">
                    Could not load the catalogue.
                    Check that FastAPI is running and the API
                    response has the expected pagination format.
                </td>
            </tr>
        `;

        pageInfo.textContent = "Catalogue unavailable";
        pageNumber.textContent = "Page —";

        document.getElementById("song-count").textContent = "—";
        document.getElementById("segment-count").textContent = "—";
    }
}
// Previous page.
document.getElementById("songs-prev").addEventListener(
    "click",
    async () => {
        if (songsCurrentPage > 1) {
            songsCurrentPage--;
            await loadSongs();
        }
    }
);

// Next page.
document.getElementById("songs-next").addEventListener(
    "click",
    async () => {
        if (songsCurrentPage < songsTotalPages) {
            songsCurrentPage++;
            await loadSongs();
        }
    }
);

// Change the number of songs shown per page.
document.getElementById("songs-page-size").addEventListener(
    "change",
    async (event) => {
        songsPageSize = Number(event.target.value);
        songsCurrentPage = 1;

        await loadSongs();
    }
);


// ============================================================
// DISPLAY SIMILARITY RESULTS
// ============================================================

function renderSearchResults(data) {
    const results = Array.isArray(data.results) ? data.results : [];

    resultsTable.replaceChildren();

    resultsSummary.textContent = `Search #${data.search_id}`;

    resultsMetadata.textContent =
        `${formatNumber(data.note_count)} melodic notes analyzed · ` +
        `${formatNumber(data.segments_searched)} uploaded segments searched · ` +
        `${formatNumber(data.songs_count ?? 0)} matching songs · ` +
        `${formatNumber(results.length)} matching segments`;

    if (results.length === 0) {
        resultsTableWrapper.classList.add("hidden");
        resultsEmpty.classList.remove("hidden");

        resultsEmpty.textContent =
            "No matching reference segments were found.";

        return;
    }

    resultsEmpty.classList.add("hidden");
    resultsTableWrapper.classList.remove("hidden");

    // Group matching segments by reference song.
    const groupedSongs = new Map();

    for (const result of results) {
        const key =
            `${result.song_id ?? result.song_title}|||${result.artist || "Unknown artist"}`;

        if (!groupedSongs.has(key)) {
            groupedSongs.set(key, {
                title: result.song_title || "Unknown song",
                artist: result.artist || "Unknown artist",
                segments: [],
                matchRatio: Number(result.match_ratio || 0),
                matchingSegments: Number(result.matching_segments || 0),
                totalUploadedSegments: Number(
                    result.total_uploaded_segments ?? data.segments_searched ?? 0
                )
            });
        }

        const song = groupedSongs.get(key);

        song.segments.push(result);


        // Keep the song-level match metrics supplied by the backend.
        song.matchRatio = Number(result.match_ratio ?? song.matchRatio);
        song.matchingSegments = Number(
            result.matching_segments ?? song.matchingSegments
        );
        song.totalUploadedSegments = Number(
            result.total_uploaded_segments ?? song.totalUploadedSegments
        );
    }

    const songs = Array.from(groupedSongs.values());

    // Rank songs by match ratio, highest first.
    songs.sort((a, b) => b.matchRatio - a.matchRatio);

    // Display one expandable row per song.
    for (const song of songs) {
        const songRow = document.createElement("tr");
        songRow.className = "song-result-row";

        const songCell = document.createElement("td");
        songCell.colSpan = 1;

        const toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "song-expand-button";
        toggle.setAttribute("aria-expanded", "false");

        const arrow = document.createElement("span");
        arrow.className = "expand-arrow";
        arrow.textContent = "▶";

        const songDetails = document.createElement("span");
        songDetails.className = "song-details";

        const songTitle = document.createElement("strong");
        songTitle.textContent = song.title;

        const artistName = document.createElement("span");
        artistName.className = "artist-name";
        artistName.textContent = song.artist;

        songDetails.append(songTitle, artistName);
        toggle.append(arrow, songDetails);
        songCell.appendChild(songDetails);

        const segmentCountCell = document.createElement("td");
        segmentCountCell.className = "match-summary-cell";

        const displayedSegmentCount = document.createElement("div");
        displayedSegmentCount.textContent =
            `${formatNumber(song.matchingSegments)} / ` +
            `${formatNumber(song.totalUploadedSegments)} uploaded segments matched`;

        segmentCountCell.appendChild(displayedSegmentCount);

        const matchRatioCell = document.createElement("td");
        matchRatioCell.className = "similarity-value";
        matchRatioCell.textContent = formatScore(song.matchRatio);

        songRow.append(songCell, segmentCountCell, matchRatioCell);
        resultsTable.appendChild(songRow);

    }

    resultsSection.scrollIntoView({
        behavior: "smooth",
        block: "start"
    });
}


// ============================================================
// UPLOAD MIDI AND RUN SIMILARITY SEARCH
// ============================================================

uploadForm.addEventListener("submit", async (event) => {
    event.preventDefault();

    clearMessage();

    if (!selectedMidiFile) {
        showMessage("Please select a MIDI file first.", "error");
        return;
    }

    const formData = new FormData();
    formData.append("file", selectedMidiFile);

    uploadButton.disabled = true;
    uploadButton.textContent = "Searching for similar melodies...";

    resultsSummary.textContent = "Searching...";
    resultsMetadata.textContent = "Analyzing uploaded MIDI...";
    resultsEmpty.textContent = "Please wait while the search runs.";
    resultsEmpty.classList.remove("hidden");
    resultsTableWrapper.classList.add("hidden");
    resultsTable.replaceChildren();

    try {
        const response = await fetch(`${API_BASE}/api/upload`, {
            method: "POST",
            body: formData
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || "Upload and search failed.");
        }

        if (data.status === "completed") {
            renderSearchResults(data);

            showMessage(
                `Search completed successfully. ` +
                `${formatNumber(data.results_count)} potential matches found.`
            );

        } else {
            throw new Error(
                `Search #${data.search_id} returned status: ` +
                `${data.status || "unknown"}.`
            );
        }

    } catch (error) {
        showMessage(
            error.message || "An unexpected error occurred.",
            "error"
        );

        resultsSummary.textContent = "Search failed";
        resultsMetadata.textContent = "The search could not be completed.";
        resultsEmpty.textContent =
            "Check the error message above and the backend terminal.";
        resultsEmpty.classList.remove("hidden");
        resultsTableWrapper.classList.add("hidden");

    } finally {
        uploadButton.disabled = false;
        uploadButton.innerHTML = 'Upload and Analyze <span>→</span>';
    }
});


// ============================================================
// REFRESH BUTTON
// ============================================================

document.getElementById("refresh-button").addEventListener(
    "click",
    async () => {
        await loadSongs();
        await checkHealth();
    }
);


// ============================================================
// INITIALIZE DASHBOARD
// ============================================================

async function initialize() {
    await Promise.all([
        checkHealth(),
        loadSongs()
    ]);
}

initialize();