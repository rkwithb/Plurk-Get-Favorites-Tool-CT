# Copyright (c) 2026 rkwithb (https://github.com/rkwithb)
# Licensed under CC BY-NC 4.0 (Non-Commercial Use Only)
# Disclaimer: Use at your own risk. The author is not responsible for any damages.

"""
core/db.py

SQLite database operations for plurk-fav.
- init_db()         : open connection, create tables, run migration if needed
- save_to_db()      : insert a single plurk (INSERT OR IGNORE)
- get_last_saved_id(): return the highest plurk_id in the DB (0 if empty)
- get_total_count() : return total row count in favorites

Migration strategy (old schema: plurk_id, posted, raw_json only):
- Detect missing columns via PRAGMA table_info
- Add missing columns with ALTER TABLE
- Backfill owner_id, nick_name, plurk_type from raw_json — no API calls needed
- Migration is a one-time cost; subsequent launches skip it silently

All migration log messages are emitted via an on_log callback so they surface
in the GUI log area rather than printing to stdout.
"""

import json
import sqlite3
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


def _migrate(conn: sqlite3.Connection, on_log: Callable[[str], None]) -> None:
    """
    Detect old schema and apply ALTER TABLE + backfill if needed.
    Emits log messages via on_log so they appear in the GUI log area.

    Old schema:  plurk_id, posted, raw_json
    New columns: owner_id, nick_name, plurk_type
    """
    cursor = conn.cursor()
    existing = _get_existing_columns(cursor)
    new_columns = {"owner_id", "nick_name", "plurk_type"}

    missing = new_columns - existing
    if not missing:
        # Schema is already up to date — nothing to do
        return

    from core.i18n import t
    on_log(t("log_db_migrating"))
    logger.info("db: schema migration required — missing columns: %s", missing)

    # Add missing columns (ALTER TABLE cannot add multiple columns in one statement)
    type_map = {
        "owner_id":   "INTEGER",
        "nick_name":  "TEXT",
        "plurk_type": "INTEGER",
    }
    for col in missing:
        cursor.execute(f"ALTER TABLE favorites ADD COLUMN {col} {type_map[col]}")
        logger.debug("db: added column '%s'", col)

    conn.commit()

    # Backfill from raw_json — one pass, no API calls
    cursor.execute("SELECT plurk_id, raw_json FROM favorites WHERE owner_id IS NULL")
    rows = cursor.fetchall()
    logger.info("db: backfilling %d rows", len(rows))

    for i, (plurk_id, raw) in enumerate(rows):
        try:
            p = json.loads(raw)
            cursor.execute(
                "UPDATE favorites SET owner_id=?, nick_name=?, plurk_type=? WHERE plurk_id=?",
                (
                    p.get("owner_id"),
                    p.get("nick_name", ""),
                    p.get("plurk_type"),
                    plurk_id,
                )
            )
        except Exception as e:
            # Log and skip — a single bad row should not abort the whole migration
            logger.warning("db: backfill failed for plurk_id=%s — %s", plurk_id, e)

        # Commit every 200 rows so progress is preserved if the user closes
        # the program mid-migration. The WHERE owner_id IS NULL filter in the
        # query above acts as a natural resume point on the next launch.
        if i % 200 == 199:
            conn.commit()
            logger.debug("db: backfill checkpoint at row %d", i + 1)

    conn.commit()  # Flush any remaining rows in the final partial batch
    logger.info("db: migration complete")
    on_log(t("log_db_migration_done"))


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
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    cursor = conn.cursor()

    # -- favorites table ------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS favorites (
            plurk_id   INTEGER PRIMARY KEY,
            posted     TEXT,
            owner_id   INTEGER,
            nick_name  TEXT,
            plurk_type INTEGER,
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

    conn.commit()
    logger.debug("db: tables and indexes ready")

    # Run migration only if the DB already existed with the old schema
    _migrate(conn, on_log)

    return conn


def save_to_db(
    conn: sqlite3.Connection,
    plurk_id: int,
    posted: str,
    owner_id: int,
    nick_name: str,
    plurk_type: int,
    raw_json: str,
) -> None:
    """
    Insert a single plurk record.
    Silently skips duplicates (INSERT OR IGNORE on PRIMARY KEY).

    Args:
        conn:       open database connection
        plurk_id:   Plurk's unique post ID
        posted:     post timestamp string from API, e.g. "Fri, 05 Jun 2009 06:00:00 GMT"
        owner_id:   numeric user ID of the post owner
        nick_name:  display name of the post owner, denormalised at backup time
        plurk_type: 0=public, 1=private, 4=anonymous
        raw_json:   full API response dict serialised as a JSON string
    """
    conn.execute(
        """
        INSERT OR IGNORE INTO favorites
            (plurk_id, posted, owner_id, nick_name, plurk_type, raw_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (plurk_id, posted, owner_id, nick_name, plurk_type, raw_json),
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
