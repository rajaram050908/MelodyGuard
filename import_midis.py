from pathlib import Path
import traceback
import time

from backend import main as mg


# ============================================================
# CONFIGURATION
# ============================================================

DATASET_DIR = (
    mg.PROJECT_DIR
    / "dataset"
    / "clean_midi"
    / "clean_midi"
)

BATCH_DESCRIPTION = "Lakh MIDI Dataset bulk import"


# ============================================================
# DATABASE HELPERS
# ============================================================

def get_admin_id():
    """Find an admin account for recording import history."""

    with mg.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT admin_id
                FROM admin_users
                ORDER BY admin_id
                LIMIT 1;
                """
            )

            row = cur.fetchone()

            if row is None:
                raise RuntimeError(
                    "No admin account exists. Create an admin account first."
                )

            return row[0]


def already_imported(cur, file_path):
    """Check whether this exact dataset file is already in the library."""

    cur.execute(
        """
        SELECT song_id
        FROM songs
        WHERE midi_file = %s;
        """,
        (str(file_path.resolve()),),
    )

    return cur.fetchone()


def create_history_record(admin_id, filename):
    """Create a history record before processing the MIDI file."""

    with mg.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO reference_imports (
                    admin_id,
                    filename,
                    status,
                    songs_added,
                    segments_added
                )
                VALUES (%s, %s, 'pending', 0, 0)
                RETURNING import_id;
                """,
                (admin_id, filename),
            )

            return cur.fetchone()[0]


def update_history(import_id, status, songs_added=0,
                   segments_added=0, error_message=None):
    """Update the import history record."""

    with mg.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE reference_imports
                SET status = %s,
                    songs_added = %s,
                    segments_added = %s,
                    error_message = %s
                WHERE import_id = %s;
                """,
                (
                    status,
                    songs_added,
                    segments_added,
                    error_message[:2000] if error_message else None,
                    import_id,
                ),
            )


# ============================================================
# IMPORT ONE MIDI FILE
# ============================================================

def import_one_midi(file_path, admin_id):
    filename = file_path.name
    import_id = None

    try:
        # Avoid importing the same dataset path twice.
        with mg.get_connection() as conn:
            with conn.cursor() as cur:
                existing = already_imported(cur, file_path)

        if existing:
            return {
                "status": "skipped",
                "filename": filename,
                "song_id": existing[0],
                "segments": 0,
            }

        # Create the history record.
        import_id = create_history_record(admin_id, filename)

        # Use the existing MelodyGuard extraction pipeline.
        notes = mg.extract_candidate_melody(file_path)

        if len(notes) < mg.SEGMENT_NOTES:
            raise ValueError(
                f"Only {len(notes)} melodic notes found; "
                f"at least {mg.SEGMENT_NOTES} are required."
            )

        segments = mg.generate_segments(notes)

        if not segments:
            raise ValueError("No valid melody segments were generated.")

        # The parent directory is used as the artist label.
        # For the Lakh dataset this may be an ID rather than a real name.
        title = file_path.stem
        artist = file_path.parent.name

        database_file_path = str(file_path.resolve())

        # Calculate features before writing the song to the database.
        prepared_segments = []

        for segment in segments:
            feature_vector, intervals = mg.extract_features(segment)

            vector_text = (
                "["
                + ",".join(
                    str(float(value))
                    for value in feature_vector
                )
                + "]"
            )

            prepared_segments.append(
                (
                    title,
                    artist or None,
                    database_file_path,
                    segment["index"],
                    segment["start_time"],
                    segment["end_time"],
                    intervals.tolist(),
                    vector_text,
                )
            )

        # Insert the song and its segments in one transaction.
        with mg.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO songs (
                        title,
                        artist,
                        midi_file
                    )
                    VALUES (%s, %s, %s)
                    RETURNING song_id;
                    """,
                    (
                        title,
                        artist or None,
                        database_file_path,
                    ),
                )

                song_id = cur.fetchone()[0]

                segment_rows = [
                    (
                        row[0],
                        row[1],
                        row[2],
                        row[3],
                        row[4],
                        row[5],
                        row[6],
                        row[7],
                        song_id,
                    )
                    for row in prepared_segments
                ]

                cur.executemany(
                    """
                    INSERT INTO melody_segments (
                        song_title,
                        artist,
                        midi_file,
                        segment_index,
                        start_time,
                        end_time,
                        pitch_intervals,
                        feature_vector,
                        song_id
                    )
                    VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s::vector, %s
                    );
                    """,
                    segment_rows,
                )

        # Record success after the database transaction commits.
        update_history(
            import_id,
            status="completed",
            songs_added=1,
            segments_added=len(segments),
        )

        return {
            "status": "completed",
            "filename": filename,
            "song_id": song_id,
            "segments": len(segments),
        }

    except Exception as error:
        traceback.print_exc()

        if import_id is not None:
            try:
                update_history(
                    import_id,
                    status="failed",
                    error_message=(
                        f"{type(error).__name__}: {error}"
                    ),
                )
            except Exception:
                traceback.print_exc()

        return {
            "status": "failed",
            "filename": filename,
            "song_id": None,
            "segments": 0,
            "error": str(error),
        }


# ============================================================
# MAIN IMPORT LOOP
# ============================================================

def main():
    print("=" * 65)
    print(BATCH_DESCRIPTION)
    print("=" * 65)

    if not DATASET_DIR.is_dir():
        raise FileNotFoundError(
            f"Dataset directory not found:\n{DATASET_DIR}"
        )

    midi_files = sorted(
        path
        for path in DATASET_DIR.rglob("*")
        if path.is_file()
        and path.suffix.lower() in {".mid", ".midi"}
    )

    total = len(midi_files)

    print(f"Dataset folder: {DATASET_DIR}")
    print(f"MIDI files found: {total}")

    if total == 0:
        print("No MIDI files found. Nothing to import.")
        return

    admin_id = get_admin_id()

    print(f"History will be recorded under admin ID: {admin_id}")
    print()
    print("Starting import...")
    print("Do not close this terminal while importing.")
    print()

    completed = 0
    skipped = 0
    failed = 0
    total_segments = 0

    start_time = time.time()

    for index, file_path in enumerate(midi_files, start=1):
        result = import_one_midi(file_path, admin_id)

        status = result["status"]

        if status == "completed":
            completed += 1
            total_segments += result["segments"]

        elif status == "skipped":
            skipped += 1

        else:
            failed += 1

        print(
            f"[{index}/{total}] "
            f"{status.upper():8} "
            f"{file_path.name} "
            f"(song_id={result['song_id']}, "
            f"segments={result['segments']})",
            flush=True,
        )

        if status == "failed":
            print(f"    Error: {result.get('error')}", flush=True)

        if index % 100 == 0:
            elapsed = time.time() - start_time

            print()
            print("-" * 65)
            print(f"Progress: {index}/{total}")
            print(f"New songs imported: {completed}")
            print(f"Existing songs skipped: {skipped}")
            print(f"Failed files: {failed}")
            print(f"New segments created: {total_segments}")
            print(f"Elapsed time: {elapsed / 60:.1f} minutes")
            print("-" * 65)
            print()

    elapsed = time.time() - start_time

    print()
    print("=" * 65)
    print("IMPORT FINISHED")
    print("=" * 65)
    print(f"Files discovered:       {total}")
    print(f"Songs imported:         {completed}")
    print(f"Existing files skipped: {skipped}")
    print(f"Failed files:            {failed}")
    print(f"Segments created:        {total_segments}")
    print(f"Total time:              {elapsed / 60:.1f} minutes")
    print("=" * 65)


if __name__ == "__main__":
    main()