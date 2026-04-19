# Copyright (c) 2026 rkwithb (https://github.com/rkwithb)
# Licensed under Apache License 2.0 (Non-Commercial Use Only)
# Disclaimer: Use at your own risk. The author is not responsible for any damages.

"""
core/db.py

SQLite database operations for plurk-fav.
- init_db()         : open connection, create tables, run migration if needed
- save_to_db()      : insert a single plurk (INSERT OR IGNORE)
- get_last_saved_id(): return the highest plurk_id in the DB (0 if empty)
- get_total_count() : return total row count in favorites

Migration strategy (modular, extensible pipeline):
- Detect missing columns via PRAGMA table_info
- Add missing columns with ALTER TABLE
- Run independent backfill handlers for:
  * owner_id, nick_name, plurk_type from raw_json (very old DBs)
  * posted2 (ISO 8601) from posted (RFC 2822)
  * content_raw from raw_json
- Each handler is resumable and runs only if needed
- Migration is a one-time cost; subsequent launches skip what's already done

All migration log messages are emitted via an on_log callback so they surface
in the GUI log area rather than printing to stdout.
"""

import json
import sqlite3
from datetime import datetime
from typing import Callable

from core.logger import get_logger

logger = get_logger()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_existing_columns(cursor: sqlite3.Cursor) -> set[str]:
    """Return the set of column names currently in the favorites table."""
    cursor.execute("PRAGMA table_info(favorites)")
    return {row[1] for row in cursor.fetchall()}


def _has_null_data(cursor: sqlite3.Cursor, column: str) -> bool:
    """Check if a column has any NULL or empty values that need backfilling."""
    cursor.execute(f"SELECT COUNT(*) FROM favorites WHERE {column} IS NULL OR {column} = ''")
    return cursor.fetchone()[0] > 0


def _backfill_metadata_from_raw_json(
    conn: sqlite3.Connection,
    cursor: sqlite3.Cursor,
    on_log: Callable[[str], None],
) -> None:
    """
    Backfill owner_id, nick_name, plurk_type from raw_json.

    Used for very old DBs that only have (plurk_id, posted, raw_json).
    Handles the initial migration from the CLI version.

    Resumable: WHERE owner_id IS NULL ensures only unprocessed rows are touched.
    """
    cursor.execute("SELECT plurk_id, raw_json FROM favorites WHERE owner_id IS NULL")
    rows = cursor.fetchall()

    if not rows:
        return

    on_log(f"Backfilling metadata ({len(rows)} rows)...")
    logger.info("db: backfilling metadata for %d rows", len(rows))

    for i, (plurk_id, raw) in enumerate(rows):
        try:
            p = json.loads(raw)
            cursor.execute(
                "UPDATE favorites SET owner_id=?, nick_name=?, plurk_type=? WHERE plurk_id=?",
                (p.get("owner_id"), p.get("nick_name", ""), p.get("plurk_type"), plurk_id),
            )
        except Exception as e:
            logger.warning("db: metadata backfill failed for plurk_id=%s — %s", plurk_id, e)

        # Checkpoint every 200 rows for resumability
        if (i + 1) % 200 == 0:
            conn.commit()
            logger.debug("db: metadata checkpoint at row %d/%d", i + 1, len(rows))

    conn.commit()


def _backfill_posted2_from_posted(
    conn: sqlite3.Connection,
    cursor: sqlite3.Cursor,
    on_log: Callable[[str], None],
) -> None:
    """
    Backfill posted2 (ISO 8601) from posted (RFC 2822).

    No API calls needed; purely local date format conversion:
    "Sun, 23 Jun 2013 10:24:51 GMT" → "2013-06-23 10:24:51"

    Used for sorting and month-based filtering in exports.
    Resumable: WHERE posted2 IS NULL ensures only unprocessed rows are touched.
    """
    cursor.execute("SELECT plurk_id, posted FROM favorites WHERE posted2 IS NULL")
    rows = cursor.fetchall()

    if not rows:
        return

    on_log(f"Backfilling posted2 ({len(rows)} rows)...")
    logger.info("db: backfilling posted2 for %d rows", len(rows))

    for i, (plurk_id, posted) in enumerate(rows):
        try:
            posted2 = datetime.strptime(
                posted, "%a, %d %b %Y %H:%M:%S GMT"
            ).strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute("UPDATE favorites SET posted2=? WHERE plurk_id=?", (posted2, plurk_id))
        except Exception as e:
            logger.warning("db: posted2 backfill failed for plurk_id=%s — %s", plurk_id, e)

        # Checkpoint every 200 rows for resumability
        if (i + 1) % 200 == 0:
            conn.commit()
            logger.debug("db: posted2 checkpoint at row %d/%d", i + 1, len(rows))

    conn.commit()


def _backfill_content_raw_from_raw_json(
    conn: sqlite3.Connection,
    cursor: sqlite3.Cursor,
    on_log: Callable[[str], None],
) -> None:
    """
    Backfill content_raw from raw_json.

    Added in v2 to store the actual plurk content separately for storage efficiency.
    Extracts content_raw field from the API response stored in raw_json.
    If content_raw is NULL in the API (common for restricted/deleted posts),
    stores empty string for consistent data.

    Resumable: WHERE content_raw IS NULL OR content_raw = '' ensures
    only unprocessed rows are touched.
    """
    cursor.execute("SELECT plurk_id, raw_json FROM favorites WHERE content_raw IS NULL OR content_raw = ''")
    rows = cursor.fetchall()

    if not rows:
        return

    on_log(f"Backfilling content_raw ({len(rows)} rows)...")
    logger.info("db: backfilling content_raw for %d rows", len(rows))

    for i, (plurk_id, raw) in enumerate(rows):
        try:
            p = json.loads(raw)
            # Use `or ""` to handle None: if API returns null, store empty string
            content_raw = p.get("content_raw") or ""
            cursor.execute("UPDATE favorites SET content_raw=? WHERE plurk_id=?", (content_raw, plurk_id))
        except Exception as e:
            logger.warning("db: content_raw backfill failed for plurk_id=%s — %s", plurk_id, e)

        # Checkpoint every 200 rows for resumability
        if (i + 1) % 200 == 0:
            conn.commit()
            logger.debug("db: content_raw checkpoint at row %d/%d", i + 1, len(rows))

    conn.commit()


def _migrate(conn: sqlite3.Connection, on_log: Callable[[str], None]) -> None:
    """
    Coordinate all schema migrations in a modular, extensible pipeline.

    Flow:
    1. Add any missing columns (ALTER TABLE)
    2. Run independent backfill handlers:
       - Each handler checks what needs backfilling
       - Only runs if data is actually missing
       - Is resumable (idempotent)
       - Can be extended with new handlers without touching existing code

    This design supports:
    - Very old DBs (only plurk_id, posted, raw_json)
    - Intermediate DBs (metadata backfilled, but no content_raw yet)
    - Current DBs (all columns present and populated)
    """
    cursor = conn.cursor()
    existing = _get_existing_columns(cursor)
    all_columns = {"owner_id", "nick_name", "plurk_type", "posted2", "content_raw"}

    missing = all_columns - existing

    if not missing:
        # Schema is already up to date — nothing to do
        return

    from core.i18n import t
    on_log(t("log_db_migrating"))
    logger.info("db: schema migration required — missing columns: %s", missing)

    # ========== Stage 1: Add missing columns ==========
    type_map = {
        "owner_id":    "INTEGER",
        "nick_name":   "TEXT",
        "plurk_type":  "INTEGER",
        "posted2":     "TEXT",
        "content_raw": "TEXT",
    }
    for col in missing:
        cursor.execute(f"ALTER TABLE favorites ADD COLUMN {col} {type_map[col]}")
        logger.debug("db: added column '%s'", col)

    conn.commit()

    # ========== Stage 2: Run independent backfill handlers ==========
    # Each handler is independent and resumable

    if "owner_id" in missing or _has_null_data(cursor, "owner_id"):
        _backfill_metadata_from_raw_json(conn, cursor, on_log)

    if "posted2" in missing or _has_null_data(cursor, "posted2"):
        _backfill_posted2_from_posted(conn, cursor, on_log)

    if "content_raw" in missing or _has_null_data(cursor, "content_raw"):
        _backfill_content_raw_from_raw_json(conn, cursor, on_log)

    on_log(t("log_db_migration_done"))
    logger.info("db: migration complete")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def init_db(
    db_path: str,
    on_log: Callable[[str], None] = lambda msg: None,
) -> sqlite3.Connection:
    """
    Open (or create) the SQLite database, create all tables, and run
    schema migration if the existing DB has the old column layout.

    Args:
        db_path: absolute path to the .db file
        on_log:  callback that accepts a single string — emits migration
                 progress to the GUI log area. Defaults to a no-op so
                 callers that don't need log output can omit it.

    Returns:
        An open sqlite3.Connection with foreign keys enabled.
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL mode allows concurrent reads and one writer without locking errors,
    # so backup writes and tag writes from Flask can coexist safely.
    conn.execute("PRAGMA journal_mode=WAL")
    cursor = conn.cursor()

    # -- favorites table ------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS favorites (
            plurk_id   INTEGER PRIMARY KEY,
            posted     TEXT,
            posted2    TEXT,
            owner_id   INTEGER,
            nick_name  TEXT,
            plurk_type INTEGER,
            content_raw TEXT,
            raw_json   TEXT
        )
    """)

    # -- tags table -----------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )
    """)

    # -- plurk_tags join table ------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS plurk_tags (
            plurk_id INTEGER REFERENCES favorites(plurk_id),
            tag_id   INTEGER REFERENCES tags(id),
            PRIMARY KEY (plurk_id, tag_id)
        )
    """)

    # -- indexes --------------------------------------------------------
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_owner ON favorites(owner_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_type ON favorites(plurk_type)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_posted2 ON favorites(posted2)"
    )

    conn.commit()
    logger.debug("db: tables and indexes ready")

    # Run migration only if the DB already existed with the old schema
    _migrate(conn, on_log)

    return conn


def save_to_db(
    conn: sqlite3.Connection,
    plurk_id: int,
    posted: str,
    posted2: str,
    owner_id: int,
    nick_name: str,
    plurk_type: int,
    content_raw: str,
    raw_json: str,
) -> None:
    """
    Insert a single plurk record.
    Silently skips duplicates (INSERT OR IGNORE on PRIMARY KEY).

    Args:
        conn:       open database connection
        plurk_id:   Plurk's unique post ID
        posted:     post timestamp string from API, e.g. "Fri, 05 Jun 2009 06:00:00 GMT"
        posted2:    post timestamp in ISO 8601 format, e.g. "2009-06-05 06:00:00"
                    used for SQL-level month filtering in export
        owner_id:   numeric user ID of the post owner
        nick_name:  display name of the post owner, denormalised at backup time
        plurk_type: 0=public, 1=private, 4=anonymous
        content_raw: actual plurk content, extracted from API response
        raw_json:   full API response dict serialised as a JSON string
    """
    conn.execute(
        """
        INSERT OR IGNORE INTO favorites
            (plurk_id, posted, posted2, owner_id, nick_name, plurk_type, content_raw, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (plurk_id, posted, posted2, owner_id, nick_name, plurk_type, content_raw, raw_json),
    )
    conn.commit()


def get_last_saved_id(conn: sqlite3.Connection) -> int:
    """
    Return the highest plurk_id currently stored in the database.
    Returns 0 if the table is empty (signals a first-run full backup).
    """
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(plurk_id) FROM favorites")
    result = cursor.fetchone()[0]
    return result if result is not None else 0


def get_total_count(conn: sqlite3.Connection) -> int:
    """Return the total number of rows in the favorites table."""
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM favorites")
    return cursor.fetchone()[0]
