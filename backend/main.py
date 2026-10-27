from pathlib import Path
import os
import uuid
from pwdlib import PasswordHash
from pydantic import BaseModel, Field
import traceback
import time
import jwt
from datetime import datetime, timedelta, timezone
from pwdlib import PasswordHash
from pydantic import BaseModel, Field

import numpy as np
import pretty_midi
import psycopg

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Depends,Form
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# ============================================================
# CONFIGURATION
# ============================================================

PROJECT_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = PROJECT_DIR / "uploads"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "melodyguard",
    "user": "postgres",
}

DB_PASSWORD = os.environ.get("MELODYGUARD_DB_PASSWORD")

if not DB_PASSWORD:
    raise RuntimeError(
        "Set the MELODYGUARD_DB_PASSWORD environment variable "
        "before starting the backend."
    )

VECTOR_DIM = 64
SEGMENT_NOTES = 16
SEGMENT_HOP = 4
TOP_K = 10
MATCH_THRESHOLD = 0.80
MAX_UPLOAD_SIZE = 20 * 1024 * 1024


# ============================================================
# ADMIN AUTHENTICATION
# ============================================================

password_hash = PasswordHash.recommended()

JWT_SECRET = os.environ.get("MELODYGUARD_JWT_SECRET")

if not JWT_SECRET:
    raise RuntimeError(
        "Set the MELODYGUARD_JWT_SECRET environment variable."
    )

JWT_ALGORITHM = "HS256"
ADMIN_TOKEN_EXPIRE_MINUTES = 60

password_hash = PasswordHash.recommended()


class AdminLoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=1, max_length=128)


class AdminCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=12, max_length=128)



# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="MelodyGuard API",
    description="Database-backed MIDI similarity search",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5500",
        "http://127.0.0.1:5500",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)


# ============================================================
# DATABASE
# ============================================================

def get_connection():
    return psycopg.connect(
        **DB_CONFIG,
        password=DB_PASSWORD,
    )


# ============================================================
# MELODY EXTRACTION
# Same baseline method used to create reference segments
# ============================================================

def extract_candidate_melody(midi_path):
    midi = pretty_midi.PrettyMIDI(str(midi_path))

    instruments = [
        instrument
        for instrument in midi.instruments
        if not instrument.is_drum and instrument.notes
    ]

    if not instruments:
        raise ValueError("No melodic instruments were found.")

    instrument = max(
        instruments,
        key=lambda item: len(item.notes),
    )

    return sorted(
        instrument.notes,
        key=lambda note: (note.start, note.pitch),
    )


# ============================================================
# SEGMENTATION
# ============================================================

def generate_segments(notes):
    segments = []

    if len(notes) < SEGMENT_NOTES:
        return segments

    for start_index in range(
        0,
        len(notes) - SEGMENT_NOTES + 1,
        SEGMENT_HOP,
    ):
        segment_notes = notes[
            start_index:start_index + SEGMENT_NOTES
        ]

        segments.append({
            "index": start_index // SEGMENT_HOP,
            "start_index": start_index,
            "start_time": float(segment_notes[0].start),
            "end_time": float(
                max(note.end for note in segment_notes)
            ),
            "notes": segment_notes,
        })

    return segments


# ============================================================
# FEATURE EXTRACTION
# Produces 64 dimensions, matching the reference baseline
# ============================================================

def extract_features(segment):
    notes = segment["notes"]

    pitches = np.array(
        [note.pitch for note in notes],
        dtype=np.float32,
    )

    onsets = np.array(
        [note.start for note in notes],
        dtype=np.float32,
    )

    durations = np.array(
        [
            max(0.0, note.end - note.start)
            for note in notes
        ],
        dtype=np.float32,
    )

    intervals = np.diff(pitches)

    # 15 pitch-interval features
    interval_features = np.clip(
        intervals / 12.0,
        -1.0,
        1.0,
    )

    # 16 relative onset-gap features
    onset_gaps = np.diff(onsets)

    positive_gaps = onset_gaps[onset_gaps > 0]

    reference_gap = (
        float(np.median(positive_gaps))
        if len(positive_gaps) > 0
        else 1.0
    )

    if reference_gap <= 0:
        reference_gap = 1.0

    rhythm_features = np.log1p(
        np.maximum(onset_gaps, 0.0) / reference_gap
    )

    rhythm_features = np.clip(
        rhythm_features / 3.0,
        0.0,
        1.0,
    )

    # Match the original extractor's padding convention.
    rhythm_features = np.pad(
        rhythm_features,
        (0, 16 - len(rhythm_features)),
    )

    # 16 duration features
    positive_durations = durations[durations > 0]

    reference_duration = (
        float(np.median(positive_durations))
        if len(positive_durations) > 0
        else 1.0
    )

    if reference_duration <= 0:
        reference_duration = 1.0

    duration_features = np.clip(
        np.log1p(durations / reference_duration) / 3.0,
        0.0,
        1.0,
    )

    # 16 contour features
    contour_features = np.pad(
        np.sign(intervals),
        (0, 16 - len(intervals)),
    )

    # One pitch-range feature
    pitch_range = np.clip(
        float(np.ptp(pitches)) / 12.0,
        0.0,
        1.0,
    )

    vector = np.concatenate([
        np.pad(
            interval_features,
            (0, 15 - len(interval_features)),
        ),
        rhythm_features,
        duration_features,
        contour_features,
        np.array([pitch_range], dtype=np.float32),
    ]).astype(np.float32)

    if vector.shape != (VECTOR_DIM,):
        raise ValueError(
            f"Expected 64 features, received {vector.shape}."
        )

    if not np.all(np.isfinite(vector)):
        raise ValueError("Feature vector contains invalid values.")

    return vector, intervals.astype(np.float32)


# ============================================================
# SIMILARITY SEARCH
# ============================================================

# ============================================================
# SIMILARITY SEARCH
# Top 10 songs, with top 10 segments for each song
# ============================================================
def search_database(connection, query_segments):
    import heapq
    import time

    total_uploaded_segments = len(query_segments)

    if total_uploaded_segments == 0:
        return []

    # Store one best match per uploaded segment for each song.
    best_match_per_song_query = {}

    # Store only the best 10 segment matches per song.
    top_segments_per_song = {}

    song_metadata = {}

    with connection.cursor() as cur:
        for query_segment_index, query_segment in enumerate(query_segments):

            vector, query_intervals = extract_features(query_segment)

            vector_text = "[" + ",".join(
                str(float(value)) for value in vector
            ) + "]"

            query_start = time.perf_counter()

            cur.execute("""
                SELECT
                    ms.id AS matched_segment_id,
                    ms.song_id,
                    s.title AS song_title,
                    s.artist,
                    ms.segment_index,
                    ms.start_time AS matched_start_time,
                    ms.end_time AS matched_end_time,
                    ms.pitch_intervals,
                    ms.feature_vector <=> %s::vector
                        AS cosine_distance
                FROM melody_segments AS ms
                JOIN songs AS s
                    ON s.song_id = ms.song_id
                WHERE ms.feature_vector IS NOT NULL
                ORDER BY ms.feature_vector <=> %s::vector
                LIMIT 9000;
            """, (
                vector_text,
                vector_text,
            ))

            candidates = cur.fetchall()

            print(
                f"Segment {query_segment_index + 1}: "
                f"{len(candidates)} candidates retrieved in "
                f"{time.perf_counter() - query_start:.3f} seconds"
            )

            uploaded_start = float(query_segment["start_time"])
            uploaded_end = float(query_segment["end_time"])

            for candidate in candidates:
                distance = max(0.0, float(candidate[8]))

                # Preserve the existing vector score formula.
                vector_score = float(np.exp(-distance / 0.005))

                reference_intervals = candidate[7] or []

                # Preserve the existing interval score formula.
                if (
                    len(query_intervals) > 0
                    and len(query_intervals) == len(reference_intervals)
                ):
                    interval_differences = np.abs(
                        np.asarray(
                            query_intervals,
                            dtype=np.float32
                        )
                        - np.asarray(
                            reference_intervals,
                            dtype=np.float32
                        )
                    )

                    interval_score = float(
                        np.mean(
                            np.exp(-interval_differences / 2.0)
                        )
                    )
                else:
                    interval_score = 0.0

                # Preserve the existing ranking score formula.
                ranking_score = (
                    0.7 * vector_score
                    + 0.3 * interval_score
                )

                ranking_score = max(
                    0.0,
                    min(1.0, ranking_score)
                )

                song_id = candidate[1]
                query_index = query_segment_index

                song_metadata[song_id] = {
                    "song_id": song_id,
                    "song_title": candidate[2],
                    "artist": candidate[3],
                }

                result = {
                    "query_segment_index": query_index,
                    "matched_segment_id": candidate[0],
                    "song_id": song_id,
                    "song_title": candidate[2],
                    "artist": candidate[3],
                    "segment_index": candidate[4],
                    "matched_start_time": float(candidate[5]),
                    "matched_end_time": float(candidate[6]),
                    "uploaded_start_time": uploaded_start,
                    "uploaded_end_time": uploaded_end,
                    "cosine_distance": distance,
                    "vector_score": vector_score,
                    "interval_score": interval_score,
                    "ranking_score": ranking_score,
                }

                # Keep the strongest match for this uploaded segment
                # within this reference song.
                query_key = (song_id, query_index)

                previous = best_match_per_song_query.get(query_key)

                if (
                    previous is None
                    or ranking_score > previous["ranking_score"]
                ):
                    best_match_per_song_query[query_key] = result

                # Keep only the top 10 segment matches per song.
                # Heap entries use the score and a unique counter
                # to avoid comparing dictionaries when scores tie.
                if song_id not in top_segments_per_song:
                    top_segments_per_song[song_id] = []

                heap = top_segments_per_song[song_id]

                entry = (
                    ranking_score,
                    query_segment_index,
                    candidate[0],
                    result,
                )

                if len(heap) < 10:
                    heapq.heappush(heap, entry)
                elif ranking_score > heap[0][0]:
                    heapq.heapreplace(heap, entry)

    # --------------------------------------------------------
    # Group each song's best match for every uploaded segment.
    # --------------------------------------------------------

    grouped_songs = {}

    for (song_id, query_index), result in best_match_per_song_query.items():
        if song_id not in grouped_songs:
            grouped_songs[song_id] = {
                **song_metadata[song_id],
                "matching_segments": 0,
                "total_uploaded_segments": total_uploaded_segments,
                "best_query_matches": {},
            }

        grouped_songs[song_id]["best_query_matches"][query_index] = result

        if result["ranking_score"] >= MATCH_THRESHOLD:
            grouped_songs[song_id]["matching_segments"] += 1

    # --------------------------------------------------------
    # Calculate match ratio for each song.
    # --------------------------------------------------------

    for song_id, song in grouped_songs.items():
        song["match_ratio"] = (
            song["matching_segments"]
            / total_uploaded_segments
            * 100.0
        )

        top_segments = [
            entry[3]
            for entry in top_segments_per_song.get(song_id, [])
        ]

        top_segments.sort(
            key=lambda segment: segment["ranking_score"],
            reverse=True
        )

        song["segments"] = top_segments
    # --------------------------------------------------------
    # Return the top 10 songs.
    # --------------------------------------------------------

    ranked_songs = sorted(
        grouped_songs.values(),
        key=lambda song: song["match_ratio"],
        reverse=True
    )[:10]

    final_results = []

    for song_rank, song in enumerate(ranked_songs, start=1):
        for segment_rank, segment in enumerate(
            song["segments"],
            start=1
        ):
            final_results.append({
                **segment,
                "song_rank": song_rank,
                "segment_rank": segment_rank,
                "match_ratio": song["match_ratio"],
                "matching_segments": song["matching_segments"],
                "total_uploaded_segments": song["total_uploaded_segments"],
            })

    return final_results

# ============================================================
# ADMIN AUTHORIZATION
# ============================================================

admin_bearer = HTTPBearer()


def require_admin(
    credentials: HTTPAuthorizationCredentials = Depends(admin_bearer),
):
    try:
        payload = jwt.decode(
            credentials.credentials,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
        )

        admin_id = int(payload["sub"])

    except Exception:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired admin token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT admin_id, username
                FROM admin_users
                WHERE admin_id = %s;
                """,
                (admin_id,),
            )

            admin = cur.fetchone()

    if admin is None:
        raise HTTPException(
            status_code=401,
            detail="Admin account no longer exists.",
        )

    return {
        "admin_id": admin[0],
        "username": admin[1],
    }

# ============================================================
# ADMIN LOGIN
# ============================================================

@app.post("/api/admin/login")
def admin_login(request: AdminLoginRequest):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT admin_id, username, password_hash
                FROM admin_users
                WHERE username = %s;
                """,
                (request.username.strip(),),
            )

            admin = cur.fetchone()

    if admin is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid username or password.",
        )

    admin_id, username, stored_hash = admin

    if not password_hash.verify(request.password, stored_hash):
        raise HTTPException(
            status_code=401,
            detail="Invalid username or password.",
        )

    now = datetime.now(timezone.utc)

    token = jwt.encode(
        {
            "sub": str(admin_id),
            "username": username,
            "iat": now,
            "exp": now + timedelta(
                minutes=ADMIN_TOKEN_EXPIRE_MINUTES
            ),
        },
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )

    return {
        "message": "Login successful.",
        "access_token": token,
        "token_type": "bearer",
        "expires_in": ADMIN_TOKEN_EXPIRE_MINUTES * 60,
        "admin": {
            "admin_id": admin_id,
            "username": username,
        },
    }

# ============================================================
# HEALTH CHECK
# ============================================================

# ============================================================
# ADMIN DASHBOARD STATISTICS
# ============================================================

@app.get("/api/admin/stats")
def admin_dashboard_stats(admin: dict = Depends(require_admin),):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*)
                FROM songs;
            """)
            total_songs = cur.fetchone()[0]

            cur.execute("""
                SELECT COUNT(*)
                FROM melody_segments;
            """)
            total_segments = cur.fetchone()[0]

            cur.execute("""
                SELECT COUNT(*)
                FROM searches;
            """)
            total_searches = cur.fetchone()[0]

            cur.execute("""
                SELECT COUNT(*)
                FROM reference_imports
                WHERE status = 'completed';
            """)
            successful_imports = cur.fetchone()[0]

            cur.execute("""
                SELECT COUNT(*)
                FROM reference_imports
                WHERE status = 'failed';
            """)
            failed_imports = cur.fetchone()[0]

    return {
        "total_songs": total_songs,
        "total_segments": total_segments,
        "total_searches": total_searches,
        "successful_imports": successful_imports,
        "failed_imports": failed_imports,
    }

# ============================================================
# ADMIN REFERENCE LIBRARY
# ============================================================

@app.get("/api/admin/songs")
def admin_list_songs(
    page: int = 1,
    page_size: int = 20,
    search: str = "",
    sort_by: str = "title",
    sort_order: str = "asc",
    admin: dict = Depends(require_admin),
):
    """Return a single paginated page of reference songs."""

    page = max(1, page)
    page_size = max(1, min(page_size, 100))

    allowed_sort_columns = {
        "song_id": "s.song_id",
        "title": "s.title",
        "artist": "s.artist",
        "segments": "COUNT(ms.id)",
    }

    sort_column = allowed_sort_columns.get(sort_by, "s.title")
    sort_direction = "DESC" if sort_order.lower() == "desc" else "ASC"

    search = search.strip()
    offset = (page - 1) * page_size

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:

                # Count matching references.
                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM songs AS s
                    WHERE
                        %s = ''
                        OR s.title ILIKE %s
                        OR COALESCE(s.artist, '') ILIKE %s
                        OR CAST(s.song_id AS TEXT) ILIKE %s;
                    """,
                    (
                        search,
                        f"%{search}%",
                        f"%{search}%",
                        f"%{search}%",
                    ),
                )

                total = cur.fetchone()[0]

                # Fetch only the requested page.
                cur.execute(
                    f"""
                    SELECT
                        s.song_id,
                        s.title,
                        s.artist,
                        COUNT(ms.id) AS total_segments
                    FROM songs AS s
                    LEFT JOIN melody_segments AS ms
                        ON ms.song_id = s.song_id
                    WHERE
                        %s = ''
                        OR s.title ILIKE %s
                        OR COALESCE(s.artist, '') ILIKE %s
                        OR CAST(s.song_id AS TEXT) ILIKE %s
                    GROUP BY
                        s.song_id,
                        s.title,
                        s.artist
                    ORDER BY {sort_column} {sort_direction},
                             s.song_id ASC
                    LIMIT %s OFFSET %s;
                    """,
                    (
                        search,
                        f"%{search}%",
                        f"%{search}%",
                        f"%{search}%",
                        page_size,
                        offset,
                    ),
                )

                rows = cur.fetchall()

        return {
            "items": [
                {
                    "song_id": row[0],
                    "title": row[1],
                    "artist": row[2],
                    "total_segments": row[3],
                }
                for row in rows
            ],
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size,
        }

    except Exception as error:
        traceback.print_exc()

        raise HTTPException(
            status_code=500,
            detail="Could not retrieve reference library.",
        ) from error

# ============================================================
# ADMIN: ADD MIDI REFERENCE
# ============================================================

@app.post("/api/admin/references")
async def admin_add_reference(
    title: str = Form(...),
    artist: str = Form(""),
    file: UploadFile = File(...),
    admin: dict = Depends(require_admin),
):
    """Import one MIDI reference and record both successful and failed attempts."""
    filename = Path(file.filename or "unknown.mid").name
    # Check whether this MIDI filename already exists in the reference library.
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT song_id, title
                FROM songs
                WHERE LOWER(
                    regexp_replace(midi_file, '^.*[\\/]', '')
                ) = LOWER(%s)
                LIMIT 1;
                """,
                (filename,),
            )

            existing_song = cur.fetchone()

    if existing_song:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Duplicate MIDI file! '{filename}' already exists "
                f"in the reference library as '{existing_song[1]}' "
                f"(Reference ID: {existing_song[0]})."
            ),
        )
    import_id = None
    stored_path = None
    temporary_path = None

    # Create the history row first so validation/processing failures are logged.
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO reference_imports
                        (admin_id, filename, status, songs_added, segments_added)
                    VALUES (%s, %s, 'pending', 0, 0)
                    RETURNING import_id;
                    """,
                    (admin["admin_id"], filename),
                )
                import_id = cur.fetchone()[0]
    except Exception as error:
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Could not start import history record: {type(error).__name__}.",
        )

    try:
        title = title.strip()
        artist = artist.strip()

        if not filename.lower().endswith((".mid", ".midi")):
            raise HTTPException(status_code=400, detail="Please upload a .mid or .midi file.")
        if not title:
            raise HTTPException(status_code=400, detail="Song title cannot be empty.")
        if len(title) > 300 or len(artist) > 300:
            raise HTTPException(status_code=400, detail="Title and artist must be at most 300 characters.")

        contents = await file.read(MAX_UPLOAD_SIZE + 1)
        if not contents:
            raise HTTPException(status_code=400, detail="The uploaded file is empty.")
        if len(contents) > MAX_UPLOAD_SIZE:
            raise HTTPException(status_code=413, detail="The maximum MIDI file size is 20 MB.")

        import tempfile
        with tempfile.NamedTemporaryFile(suffix=Path(filename).suffix.lower(), delete=False) as temp_file:
            temp_file.write(contents)
            temporary_path = Path(temp_file.name)

        try:
            notes = extract_candidate_melody(temporary_path)
        except Exception as error:
            raise HTTPException(status_code=400, detail=f"Could not read this MIDI file: {error}") from error

        if len(notes) < SEGMENT_NOTES:
            raise HTTPException(
                status_code=400,
                detail=f"Only {len(notes)} melodic notes were found. At least {SEGMENT_NOTES} are required.",
            )

        segments = generate_segments(notes)
        if not segments:
            raise HTTPException(status_code=400, detail="No valid melody segments could be generated.")

        reference_dir = PROJECT_DIR / "reference_midis"
        reference_dir.mkdir(parents=True, exist_ok=True)
        stored_path = reference_dir / f"{uuid.uuid4().hex}{Path(filename).suffix.lower()}"
        stored_path.write_bytes(contents)
        database_file_path = str(stored_path.resolve())

        # Song, segments and successful history status commit together.
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO songs (title, artist, midi_file)
                    VALUES (%s, %s, %s)
                    RETURNING song_id;
                    """,
                    (title, artist or None, database_file_path),
                )
                song_id = cur.fetchone()[0]

                for segment in segments:
                    feature_vector, intervals = extract_features(segment)
                    vector_text = "[" + ",".join(str(float(value)) for value in feature_vector) + "]"
                    cur.execute(
                        """
                        INSERT INTO melody_segments (
                            song_title, artist, midi_file, segment_index,
                            start_time, end_time, pitch_intervals, feature_vector, song_id
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector, %s);
                        """,
                        (
                            title, artist or None, database_file_path,
                            segment["index"], segment["start_time"], segment["end_time"],
                            intervals.tolist(), vector_text, song_id,
                        ),
                    )

                cur.execute(
                    """
                    UPDATE reference_imports
                    SET status = 'completed', songs_added = 1,
                        segments_added = %s, error_message = NULL
                    WHERE import_id = %s;
                    """,
                    (len(segments), import_id),
                )

        return {
            "message": "Reference imported successfully.",
            "import_id": import_id,
            "song_id": song_id,
            "title": title,
            "artist": artist or None,
            "filename": filename,
            "segments_added": len(segments),
            "status": "completed",
        }

    except HTTPException as error:
        if stored_path is not None:
            stored_path.unlink(missing_ok=True)
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE reference_imports
                           SET status = 'failed', error_message = %s
                           WHERE import_id = %s;""",
                        (str(error.detail)[:2000], import_id),
                    )
        except Exception:
            traceback.print_exc()
        raise

    except Exception as error:
        traceback.print_exc()
        if stored_path is not None:
            stored_path.unlink(missing_ok=True)
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE reference_imports
                           SET status = 'failed', error_message = %s
                           WHERE import_id = %s;""",
                        (f"{type(error).__name__}: {error}"[:2000], import_id),
                    )
        except Exception:
            traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Reference import failed: {type(error).__name__}: {error}",
        )

    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


@app.get("/api/admin/imports")
def admin_import_history(admin: dict = Depends(require_admin)):
    """Return recent imports for the authenticated admin dashboard."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT ri.import_id, au.username, ri.filename, ri.status,
                           ri.songs_added, ri.segments_added, ri.error_message,
                           ri.created_at
                    FROM reference_imports AS ri
                    JOIN admin_users AS au ON au.admin_id = ri.admin_id
                    ORDER BY ri.created_at DESC, ri.import_id DESC;
                    """
                )
                rows = cur.fetchall()
        return [
            {
                "import_id": row[0],
                "admin_username": row[1],
                "filename": row[2],
                "status": row[3],
                "songs_added": row[4],
                "segments_added": row[5],
                "error_message": row[6],
                "created_at": row[7].isoformat() if row[7] else None,
            }
            for row in rows
        ]
    except Exception as error:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Could not retrieve import history.") from error

@app.get("/api/admin/history")
def admin_history(
    page: int = 1,
    page_size: int = 20,
    search: str = "",
    activity: str = "all",
    admin: dict = Depends(require_admin),
):
    """Return paginated reference import and deletion history efficiently."""

    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    offset = (page - 1) * page_size

    search = search.strip()
    activity = activity.lower()

    if activity not in {"all", "import", "failed", "delete"}:
        activity = "all"

    # Shared history fields. Song lookups are deliberately deferred
    # until after pagination whenever the search box is empty.
    history_cte = """
        WITH history AS (
            SELECT
                ri.import_id AS event_id,
                'import'::TEXT AS activity,
                ri.filename AS title,
                NULL::TEXT AS artist,
                ri.filename AS filename,
                ri.status,
                ri.segments_added AS segments,
                ri.error_message AS details,
                au.username AS admin_username,
                ri.created_at AS event_time,
                NULL::BIGINT AS reference_id
            FROM reference_imports AS ri
            LEFT JOIN admin_users AS au
                ON au.admin_id = ri.admin_id

            UNION ALL

            SELECT
                rd.deletion_id AS event_id,
                'delete'::TEXT AS activity,
                rd.title,
                rd.artist,
                rd.midi_filename AS filename,
                'deleted'::TEXT AS status,
                rd.segments_deleted AS segments,
                'Reference deleted'::TEXT AS details,
                COALESCE(au.username, 'Unknown') AS admin_username,
                rd.deleted_at AS event_time,
                rd.song_id AS reference_id
            FROM reference_deletions AS rd
            LEFT JOIN admin_users AS au
                ON au.admin_id = rd.admin_id
        )
    """

    pattern = f"%{search}%"

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:

                # --------------------------------------------------
                # 1. COUNT
                # Avoid song-ID lookups for the normal empty search.
                # --------------------------------------------------
                if not search:
                    cur.execute(
                        history_cte
                        + """
                        SELECT COUNT(*)
                        FROM history
                        WHERE
                            (
                                %s = 'all'
                                OR activity = %s
                                OR (%s = 'failed' AND status = 'failed')
                            )
                        """,
                        (activity, activity, activity),
                    )
                else:
                    # Searching by a reference ID is supported too.
                    # This lookup runs only when the user searches.
                    cur.execute(
                        history_cte
                        + """
                        SELECT COUNT(*)
                        FROM history AS h
                        WHERE
                            (
                                h.title ILIKE %s
                                OR COALESCE(h.artist, '') ILIKE %s
                                OR COALESCE(h.filename, '') ILIKE %s
                                OR COALESCE(h.admin_username, '') ILIKE %s
                                OR CAST(
                                    COALESCE(
                                        h.reference_id,
                                        (
                                            SELECT s.song_id
                                            FROM songs AS s
                                            WHERE regexp_replace(
                                                s.midi_file,
                                                '^.*[\\\\/]',
                                                ''
                                            ) = h.filename
                                            ORDER BY s.song_id DESC
                                            LIMIT 1
                                        )
                                    ) AS TEXT
                                ) ILIKE %s
                            )
                            AND (
                                %s = 'all'
                                OR h.activity = %s
                                OR (%s = 'failed' AND h.status = 'failed')
                            )
                        """,
                        (
                            pattern,
                            pattern,
                            pattern,
                            pattern,
                            pattern,
                            activity,
                            activity,
                            activity,
                        ),
                    )

                total = cur.fetchone()[0]

                # --------------------------------------------------
                # 2. PAGE
                # Filter, sort and paginate BEFORE resolving song IDs.
                # --------------------------------------------------
                if not search:
                    cur.execute(
                        history_cte
                        + """
                        , page_rows AS (
                            SELECT *
                            FROM history
                            WHERE
                                (
                                    %s = 'all'
                                    OR activity = %s
                                    OR (%s = 'failed' AND status = 'failed')
                                )
                            ORDER BY event_time DESC, event_id DESC
                            LIMIT %s OFFSET %s
                        )
                        SELECT
                            p.event_id,
                            p.activity,
                            p.title,
                            p.artist,
                            p.filename,
                            p.status,
                            p.segments,
                            p.details,
                            p.admin_username,
                            p.event_time,
                            COALESCE(
                                p.reference_id,
                                (
                                    SELECT s.song_id
                                    FROM songs AS s
                                    WHERE regexp_replace(
                                        s.midi_file,
                                        '^.*[\\\\/]',
                                        ''
                                    ) = p.filename
                                    ORDER BY s.song_id DESC
                                    LIMIT 1
                                )
                            ) AS reference_id
                        FROM page_rows AS p
                        ORDER BY p.event_time DESC, p.event_id DESC
                        """,
                        (
                            activity,
                            activity,
                            activity,
                            page_size,
                            offset,
                        ),
                    )
                else:
                    # Preserve filename, artist, admin and reference-ID
                    # searching. Pagination is still applied to the result.
                    cur.execute(
                        history_cte
                        + """
                        SELECT
                            h.event_id,
                            h.activity,
                            h.title,
                            h.artist,
                            h.filename,
                            h.status,
                            h.segments,
                            h.details,
                            h.admin_username,
                            h.event_time,
                            COALESCE(
                                h.reference_id,
                                (
                                    SELECT s.song_id
                                    FROM songs AS s
                                    WHERE regexp_replace(
                                        s.midi_file,
                                        '^.*[\\\\/]',
                                        ''
                                    ) = h.filename
                                    ORDER BY s.song_id DESC
                                    LIMIT 1
                                )
                            ) AS reference_id
                        FROM history AS h
                        WHERE
                            (
                                h.title ILIKE %s
                                OR COALESCE(h.artist, '') ILIKE %s
                                OR COALESCE(h.filename, '') ILIKE %s
                                OR COALESCE(h.admin_username, '') ILIKE %s
                                OR CAST(
                                    COALESCE(
                                        h.reference_id,
                                        (
                                            SELECT s.song_id
                                            FROM songs AS s
                                            WHERE regexp_replace(
                                                s.midi_file,
                                                '^.*[\\\\/]',
                                                ''
                                            ) = h.filename
                                            ORDER BY s.song_id DESC
                                            LIMIT 1
                                        )
                                    ) AS TEXT
                                ) ILIKE %s
                            )
                            AND (
                                %s = 'all'
                                OR h.activity = %s
                                OR (%s = 'failed' AND h.status = 'failed')
                            )
                        ORDER BY h.event_time DESC, h.event_id DESC
                        LIMIT %s OFFSET %s
                        """,
                        (
                            pattern,
                            pattern,
                            pattern,
                            pattern,
                            pattern,
                            activity,
                            activity,
                            activity,
                            page_size,
                            offset,
                        ),
                    )

                rows = cur.fetchall()

        return {
            "items": [
                {
                    "event_id": row[0],
                    "activity": row[1],
                    "title": row[2],
                    "artist": row[3],
                    "filename": row[4],
                    "status": row[5],
                    "segments": row[6],
                    "details": row[7],
                    "admin_username": row[8],
                    "created_at": (
                        row[9].isoformat() if row[9] else None
                    ),
                    "reference_id": row[10],
                }
                for row in rows
            ],
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size,
        }

    except Exception as error:
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail="Could not retrieve activity history.",
        ) from error
   
@app.delete("/api/admin/references/{song_id}")
def admin_delete_reference(
    song_id: int,
    admin: dict = Depends(require_admin)
):
    """Delete a reference and preserve its details in history."""

    midi_file_path = None
    title = None

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:

                # Get and lock the reference.
                cur.execute(
                    """
                    SELECT song_id, title, artist, midi_file
                    FROM songs
                    WHERE song_id = %s
                    FOR UPDATE;
                    """,
                    (song_id,),
                )

                song = cur.fetchone()

                if song is None:
                    raise HTTPException(
                        status_code=404,
                        detail="Reference song not found."
                    )

                reference_id, title, artist, midi_file_path = song

                # Count segments before deleting them.
                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM melody_segments
                    WHERE song_id = %s;
                    """,
                    (reference_id,),
                )

                segments_deleted = cur.fetchone()[0]

                # Save the deletion record.
                # This is in the same transaction as the database deletion.
                cur.execute(
                    """
                    INSERT INTO reference_deletions (
                        admin_id,
                        song_id,
                        title,
                        artist,
                        midi_filename,
                        segments_deleted
                    )
                    VALUES (%s, %s, %s, %s, %s, %s);
                    """,
                    (
                        admin["admin_id"],
                        reference_id,
                        title,
                        artist,
                        Path(midi_file_path).name if midi_file_path else None,
                        segments_deleted,
                    ),
                )

                # Remove saved search results referring to these segments.
                cur.execute(
                    """
                    DELETE FROM search_results
                    WHERE matched_segment_id IN (
                        SELECT id
                        FROM melody_segments
                        WHERE song_id = %s
                    );
                    """,
                    (reference_id,),
                )

                # Delete segments and the song.
                cur.execute(
                    "DELETE FROM melody_segments WHERE song_id = %s;",
                    (reference_id,),
                )

                cur.execute(
                    "DELETE FROM songs WHERE song_id = %s;",
                    (reference_id,),
                )

        # Delete the MIDI file only if it is inside the dedicated folder.
        file_deleted = True

        if midi_file_path:
            reference_dir = (PROJECT_DIR / "reference_midis").resolve()
            candidate = Path(midi_file_path).resolve()

            if candidate.parent == reference_dir and candidate.is_file():
                try:
                    candidate.unlink()
                except OSError:
                    traceback.print_exc()
                    file_deleted = False

        return {
            "message": (
                "Reference deleted successfully."
                if file_deleted
                else "Reference deleted from the database, but its MIDI file could not be removed."
            ),
            "song_id": reference_id,
            "title": title,
            "file_deleted": file_deleted,
        }

    except HTTPException:
        raise

    except Exception as error:
        traceback.print_exc()

        raise HTTPException(
            status_code=500,
            detail="Could not delete the reference song."
        ) from error
@app.get("/api/health")
def health_check():
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version()")
                version = cur.fetchone()[0]

        return {
            "status": "ok",
            "database": "connected",
            "postgresql_version": version,
        }

    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Database connection failed.",
        )


# ============================================================
# REFERENCE SONGS
# ============================================================

@app.get("/api/songs")
@app.get("/api/songs")
def list_songs(
    page: int = 1,
    page_size: int = 20,
):
    """Return one paginated page of reference songs."""

    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    offset = (page - 1) * page_size

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:

                # Total number of reference songs.
                cur.execute("""
                    SELECT COUNT(*)
                    FROM songs;
                """)

                total = cur.fetchone()[0]

                # Total segments across the entire catalogue.
                cur.execute("""
                    SELECT COUNT(*)
                    FROM melody_segments;
                """)

                total_segments = cur.fetchone()[0]

                # Fetch only the requested page.
                cur.execute("""
                    SELECT
                        s.song_id,
                        s.title,
                        s.artist,
                        COUNT(ms.id) AS total_segments
                    FROM songs AS s
                    LEFT JOIN melody_segments AS ms
                        ON ms.song_id = s.song_id
                    GROUP BY
                        s.song_id,
                        s.title,
                        s.artist
                    ORDER BY
                        s.title ASC,
                        s.song_id ASC
                    LIMIT %s
                    OFFSET %s;
                """, (page_size, offset))

                rows = cur.fetchall()

        total_pages = (total + page_size - 1) // page_size

        return {
            "items": [
                {
                    "song_id": row[0],
                    "title": row[1],
                    "artist": row[2],
                    "total_segments": row[3],
                }
                for row in rows
            ],
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
            "total_segments": total_segments,
        }

    except Exception:
        import traceback
        traceback.print_exc()

        raise HTTPException(
            status_code=500,
            detail="Could not retrieve reference songs.",
        )

# ============================================================
# UPLOAD AND SEARCH
# ============================================================

@app.post("/api/upload")
async def upload_midi(file: UploadFile = File(...)):
    filename = Path(file.filename or "").name

    if not filename.lower().endswith((".mid", ".midi")):
        raise HTTPException(
            status_code=400,
            detail="Please upload a .mid or .midi file.",
        )

    contents = await file.read(MAX_UPLOAD_SIZE + 1)

    if not contents:
        raise HTTPException(
            status_code=400,
            detail="The uploaded file is empty.",
        )

    if len(contents) > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=413,
            detail="The maximum upload size is 20 MB.",
        )

    stored_path = UPLOAD_DIR / f"{uuid.uuid4().hex}.mid"
    search_id = None

    try:
        stored_path.write_bytes(contents)

        notes = extract_candidate_melody(stored_path)

        if not notes:
            raise HTTPException(
                status_code=400,
                detail="No melodic notes were found.",
            )

        segments = generate_segments(notes)

        if not segments:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Only {len(notes)} melodic notes were found. "
                    f"At least {SEGMENT_NOTES} are required."
                ),
            )

        # Register the search and mark it as processing.
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO searches (
                        uploaded_filename,
                        status
                    )
                    VALUES (%s, 'processing')
                    RETURNING search_id;
                """, (filename,))

                search_id = cur.fetchone()[0]

        # Perform vector search and save results.
        with get_connection() as conn:
            matches = search_database(conn, segments)

            with conn.cursor() as cur:
                for rank, match in enumerate(matches, start=1):
                    cur.execute("""
                        INSERT INTO search_results (
                            search_id,
                            matched_segment_id,
                            uploaded_start_time,
                            uploaded_end_time,
                            similarity_score,
                            result_rank
                        )
                        VALUES (%s, %s, %s, %s, %s, %s);
                    """, (
                        search_id,
                        match["matched_segment_id"],
                        match["uploaded_start_time"],
                        match["uploaded_end_time"],
                        match["ranking_score"],
                        rank,
                    ))

                cur.execute("""
                    UPDATE searches
                    SET status = 'completed',
                        completed_at = CURRENT_TIMESTAMP,
                        error_message = NULL
                    WHERE search_id = %s;
                """, (search_id,))

        # Count distinct reference songs in the results.
        matched_song_ids = {
            match["song_id"]
            for match in matches
        }

        return {
            "search_id": search_id,
            "filename": filename,
            "status": "completed",
            "note_count": len(notes),
            "segments_searched": len(segments),
            "results_count": len(matches),
            "songs_count": len(matched_song_ids),
            "message": "Similarity search completed.",

            "results": [
                {
                    "rank": rank,
                    "song_rank": match["song_rank"],
                    "segment_rank": match["segment_rank"],

                    "song_id": match["song_id"],
                    "song_title": match["song_title"],
                    "artist": match["artist"],

                    "match_ratio": round(
                        match["match_ratio"],
                        2
                    ),

                    "matching_segments": match["matching_segments"],

                    "total_uploaded_segments": match["total_uploaded_segments"],

                    "reference_segment": match["segment_index"],

                    "reference_start_time": round(
                        match["matched_start_time"],
                        2
                    ),

                    "reference_end_time": round(
                        match["matched_end_time"],
                        2
                    ),

                    "uploaded_start_time": round(
                        match["uploaded_start_time"],
                        2
                    ),

                    "uploaded_end_time": round(
                        match["uploaded_end_time"],
                        2
                    ),

                    "cosine_distance": round(
                        match["cosine_distance"],
                        4
                    ),

                    "similarity_score": round(
                        match["ranking_score"] * 100,
                        2
                    ),
                }

                for rank, match in enumerate(matches, start=1)
            ],
        }

    except HTTPException:
        if search_id is not None:
            try:
                with get_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            UPDATE searches
                            SET status = 'failed',
                                completed_at = CURRENT_TIMESTAMP,
                                error_message = %s
                            WHERE search_id = %s;
                        """, ("Upload or search validation failed.", search_id))
            except Exception:
                pass

        stored_path.unlink(missing_ok=True)
        raise

    except Exception as error:
        traceback.print_exc()

        if search_id is not None:
            try:
                with get_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            UPDATE searches
                            SET status = 'failed',
                                completed_at = CURRENT_TIMESTAMP,
                                error_message = %s
                            WHERE search_id = %s;
                        """, (str(error)[:2000], search_id))
            except Exception:
                traceback.print_exc()

        stored_path.unlink(missing_ok=True)

        raise HTTPException(
            status_code=500,
            detail=(
                f"Similarity search failed: "
                f"{type(error).__name__}: {error}"
            ),
        )