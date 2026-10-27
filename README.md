# MelodyGuard

**A database-backed system for melodic similarity detection using MIDI files.**

MelodyGuard compares melodic passages from an uploaded MIDI file with a reference library and returns candidate songs with similar segments. It is intended as a similarity-screening tool, not as proof of legal plagiarism or authorship.

## Features

- Import MIDI files into a reference library.
- Extract a melodic track and split it into overlapping segments.
- Represent segments as numerical feature vectors.
- Store song metadata, melody segments, and vectors in PostgreSQL with the `pgvector` extension.
- Retrieve similar reference segments using vector similarity search and an HNSW index.
- Rank candidate songs and display matching results.
- Provide an admin interface for managing reference songs and viewing import/search history.

## How It Works

1. **Import reference MIDI files:** The import workflow reads MIDI files and extracts melodic information.
2. **Select and segment the melody:** The selected melodic track is split into overlapping segments. The current configuration uses 16 notes per segment and a hop size of 4 notes.
3. **Extract features:** Each segment is represented by a 64-dimensional feature vector based on musical properties used by the implementation.
4. **Store reference data:** Song metadata and segment vectors are stored in PostgreSQL using `pgvector`.
5. **Analyze a query MIDI:** An uploaded MIDI file is processed into segments in memory and compared with the stored reference library.
6. **Return candidate matches:** The system ranks candidate songs and returns matching segments and scores to the interface.

> **Important:** Query MIDI segments are processed for the search; they are not added to the reference library by the Analyze operation. Only reference imports populate the reference catalogue.

## Technology Stack

- **Python** — MIDI processing and backend logic
- **FastAPI** — backend API
- **PostgreSQL** — relational database
- **pgvector** — vector storage and similarity search
- **HNSW index** — approximate nearest-neighbour search
- **HTML, CSS, JavaScript** — admin interface

Check `requirements.txt` for the Python dependencies used by the project.
You maybe required to get additional dependencies given by your terminal

## Repository Structure

The project is organized approximately as follows. File and folder names may vary slightly depending on the current repository version.

```text
MelodyGuard/
├── backend/
│   └── main.py             # FastAPI backend
├── frontend/
│   ├── admin.html          # Admin dashboard
│   ├── admin.js            # Frontend behaviour
│   └── admin.css           # Dashboard styles
├── reference_midis/        # Local reference MIDI files (not committed)
├── dataset/                # Optional source dataset (not committed)
├── import_midis.py         # Bulk MIDI import script
├── admin.py                # Create/configure an admin account
├── requirements.txt        # Python dependencies
├── requirements-kali.txt   # Optional Kali/Linux dependencies
└── README.md
```

## Requirements

- Python 3.12 (or another version supported by the installed dependencies)
- PostgreSQL
- The `pgvector` PostgreSQL extension
- Git

You may also need a MIDI dataset or your own MIDI files to populate the reference library.

## Setup

### 1. Clone the repository

```bash
git clone <REPOSITORY_URL>
cd MelodyGuard
```

Replace `<REPOSITORY_URL>` with the repository's clone URL.

### 2. Create a virtual environment

**Windows PowerShell:**

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If PowerShell blocks activation, you can run the environment's Python directly instead:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

**Linux / Kali Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements-kali.txt
```

Use the requirements file that matches the environment and verify it contains the dependencies needed by the current backend.

### 3. Create the PostgreSQL database

Create a database named `melodyguard` using PostgreSQL tools or pgAdmin. For example, in `psql`:

```sql
CREATE DATABASE melodyguard;
```

Connect to the `melodyguard` database and enable `pgvector`:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

The PostgreSQL installation must have the `pgvector` extension installed before this command can succeed.

### 4. Configure environment variables

Set the database password and a stable JWT secret before starting the backend. Do not commit real secrets to Git.

**Windows PowerShell:**

```powershell
$env:MELODYGUARD_DB_PASSWORD = Read-Host "PostgreSQL password"
$env:MELODYGUARD_JWT_SECRET = Read-Host "Enter a long, private JWT secret"
$env:OPENBLAS_NUM_THREADS = "1"
$env:OMP_NUM_THREADS = "1"
$env:MKL_NUM_THREADS = "1"
$env:NUMEXPR_NUM_THREADS = "1"
```

Keep the same JWT secret between restarts if you want existing tokens to remain valid. These environment-variable names reflect the current local setup; check `backend/main.py` if the code has changed.

### 5. Initialize the schema

The backend may create some tables or indexes when it starts, depending on the current code version. If your repository includes a schema or migration file, run that file against the `melodyguard` database before starting the application.

Ensure the database contains the tables and indexes expected by the backend, including the reference songs, melody segments, search records, search results, and admin/import history tables. The exact schema should match the version of `backend/main.py` in the repository.

### 6. Create an admin account

If `admin.py` is the current admin-account setup script, run it from the project root using the configured virtual environment:

```powershell
python admin.py
```

Follow the prompts shown by the script. Confirm its behaviour from the source before running it, since the script may vary between versions.

### 7. Start the backend

From the project root:

```powershell
python -m uvicorn backend.main:app --reload
```

The backend API should be available at:

- API: `http://127.0.0.1:8000`
- Interactive API documentation: `http://127.0.0.1:8000/docs`

### 8. Start the frontend

Open a second terminal from the project root:

```powershell
python -m http.server 5500 --directory frontend --bind 127.0.0.1
```

Open the admin dashboard:

- `http://127.0.0.1:5500/admin.html`

Keep both the backend and frontend terminals running while using the local application.

## Adding Reference MIDI Files

Use the reference-import workflow or the repository's `import_midis.py` script to populate the reference library. Review the script and its arguments before running it, as the available options depend on the current version.

The source dataset and local reference files are not included in this repository by default. Add only data you are permitted to redistribute, and follow the dataset's licence and attribution requirements.

## Database Overview

The database separates reference data from query history:

- **`songs`** — metadata for reference songs.
- **`melody_segments`** — extracted segments and feature vectors for reference MIDI files.
- **`searches`** — records of uploaded query searches and their status.
- **`search_results`** — candidate segment matches associated with a search.
- **`admin_users`** — administrator account records.
- **`reference_imports`** — reference import history.
- **`reference_deletions`** — reference deletion audit records, if included in the current schema.

The `melody_segments` table uses a fixed-size vector representation (`vector(64)` in the current implementation). The HNSW index supports approximate nearest-neighbour retrieval with cosine distance. Check the actual schema and index definitions in your repository before relying on these details for a different version.

## Limitations

- The current workflow is designed for **MIDI files**, not direct comparison of MP3 or WAV recordings.
- Similarity scores identify candidates for further review; they do not establish legal plagiarism.
- Results depend on the extracted melody, feature representation, reference-library coverage, and scoring configuration.
- The system uses established database and music-information-retrieval techniques; its contribution is their application in a segment-wise comparison workflow.
- Instrument selection and melody extraction can affect the results, particularly for complex or multi-track MIDI files.



## Academic Use

MelodyGuard is a database systems project demonstrating relational data management, vector similarity search, indexing, and an application workflow for comparing musical segments. It is intended for educational and preliminary screening use.


