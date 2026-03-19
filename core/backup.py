# Copyright (c) 2026 rkwithb (https://github.com/rkwithb)
# Licensed under CC BY-NC 4.0 (Non-Commercial Use Only)
# Disclaimer: Use at your own risk. The author is not responsible for any damages.

"""
core/backup.py

Core backup task for plurk-fav.
- run_backup_task() : fetch favourited plurks from the Plurk API and save to DB,
                      then trigger JS export for affected months.

Modes:
    'incremental' : fetch plurks newer than the last saved plurk_id
    'date'        : fetch plurks posted on or after a given datetime
    'full'        : fetch all plurks regardless of what is already in the DB

Stop behaviour:
    stop_event is a threading.Event shared with the GUI.
    Uses stop_event.wait(timeout=1) instead of time.sleep(1) so that
    clicking [Stop] or closing the window feels instant in both cases.

Callback contracts:
    on_log(msg: str)              — emit a single log line to the GUI log area
    on_stats(this_run: int,
             total: int)          — update the stats bar live during backup
"""

import json
import sqlite3
import threading
from datetime import datetime
from typing import Callable, Optional

from plurk_oauth import PlurkAPI

from core.db import save_to_db, get_total_count
from core.export import export_js_files
from core.logger import get_logger

logger = get_logger()

# Plurk API date format
_PLURK_DATE_FMT = "%a, %d %b %Y %H:%M:%S GMT"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_posted(posted: str) -> Optional[datetime]:
    """Parse a Plurk API posted string into a datetime. Returns None on failure."""
    try:
        return datetime.strptime(posted, _PLURK_DATE_FMT)
    except Exception:
        return None


def _to_iso(posted: str) -> Optional[str]:
    """Convert a Plurk API posted string to ISO 8601. Returns None on failure."""
    dt = _parse_posted(posted)
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else None


def _resolve_nick(plurk: dict, users: dict) -> str:
    """
    Look up the nick_name for a plurk's owner from the plurk_users dict.
    Falls back to empty string if not found.

    Args:
        plurk: single plurk dict from the API response
        users: plurk_users dict from the API response, keyed by string user ID
    """
    owner_id = plurk.get("owner_id")
    if owner_id is None:
        return ""
    return users.get(str(owner_id), {}).get("nick_name", "")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_backup_task(
    client: PlurkAPI,
    conn: sqlite3.Connection,
    mode: str,
    criteria,
    backup_dir: str,
    stop_event: threading.Event,
    on_log: Callable[[str], None],
    on_stats: Callable[[int, int], None],
) -> None:
    """
    Fetch favourited plurks from the Plurk API and save to DB,
    then export JS files for all affected months.

    Args:
        client:     authorised PlurkAPI instance from auth.build_plurk_client()
        conn:       open database connection
        mode:       'incremental', 'date', or 'full'
        criteria:   stop condition value —
                      int (last plurk_id) for 'incremental' and 'full'
                      datetime for 'date'
        backup_dir: absolute path to backup_js/ for export_js_files()
        stop_event: threading.Event — set by GUI [Stop] button or window close
        on_log:     callback for GUI log area
        on_stats:   callback(this_run, total) for GUI stats bar
    """
    from core.i18n import t

    # Build human-readable mode string for the opening log line
    if mode == "incremental":
        mode_str = t("log_backup_mode_incremental")
    elif mode == "date":
        mode_str = t("log_backup_mode_date", date=criteria.strftime("%Y%m%d"))
    else:
        mode_str = t("log_backup_mode_full")

    on_log(t("log_backup_start", mode=mode_str))
    logger.info("backup: starting — mode=%s criteria=%s", mode, criteria)

    affected_months: set = set()
    offset: Optional[str] = None
    this_run: int = 0
    total: int = get_total_count(conn)

    try:
        while not stop_event.is_set():
            params = {"filter": "favorite", "limit": 30}
            if offset:
                params["offset"] = offset

            # --- API call -----------------------------------------------
            try:
                res = client.callAPI("/APP/Timeline/getPlurks", params)
            except Exception as e:
                on_log(t("log_backup_api_error", error=str(e)))
                logger.error("backup: API call failed — %s", e)
                break

            if not res or not res.get("plurks"):
                logger.debug("backup: empty response — stopping pagination")
                break

            plurks = res["plurks"]
            users  = res.get("plurk_users", {})

            # --- Process each plurk in the page -------------------------
            stop_page = False
            for p in plurks:
                posted    = p.get("posted", "")
                plurk_id  = p["plurk_id"]
                posted_dt = _parse_posted(posted)

                # -- Stop condition check --------------------------------
                if mode == "incremental" and plurk_id <= criteria:
                    stop_page = True
                    break
                if mode == "date" and posted_dt and posted_dt < criteria:
                    stop_page = True
                    break

                # -- Resolve fields for save_to_db ----------------------
                posted2   = _to_iso(posted)
                owner_id  = p.get("owner_id")
                nick_name = _resolve_nick(p, users)
                plurk_type = p.get("plurk_type", 0)
                raw_json  = json.dumps(p, ensure_ascii=False)

                save_to_db(
                    conn,
                    plurk_id=plurk_id,
                    posted=posted,
                    posted2=posted2,
                    owner_id=owner_id,
                    nick_name=nick_name,
                    plurk_type=plurk_type,
                    raw_json=raw_json,
                )

                # Track affected month for targeted JS export
                if posted_dt:
                    affected_months.add(posted_dt.strftime("%Y_%m"))

                this_run += 1
                total    += 1
                on_stats(this_run, total)

            if stop_page:
                break

            # --- Prepare offset for next page ---------------------------
            last_posted = plurks[-1].get("posted", "")
            last_dt     = _parse_posted(last_posted)
            if not last_dt:
                logger.warning("backup: could not parse last posted — stopping pagination")
                break

            offset = last_dt.isoformat()

            # Use wait() instead of sleep() so stop feels instant
            if stop_event.wait(timeout=1):
                break

        # --- Determine exit reason and emit final log line --------------
        if stop_event.is_set():
            on_log(t("log_backup_stopped"))
            logger.info("backup: stopped by user after %d new plurks", this_run)
        elif this_run == 0:
            on_log(t("log_backup_no_new"))
            logger.info("backup: no new plurks found")
        else:
            on_log(t("log_backup_done", count=this_run))
            logger.info("backup: complete — %d new plurks", this_run)

    except Exception as e:
        on_log(t("log_worker_crash"))
        logger.exception("backup: unexpected error — %s", e)
        return

    # --- Export JS files for affected months ----------------------------
    # In full mode, export all months currently in the DB
    if mode == "full":
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT strftime('%Y', posted2) || '_' || strftime('%m', posted2) FROM favorites WHERE posted2 IS NOT NULL")
        affected_months = {row[0] for row in cursor.fetchall()}

    export_js_files(conn, backup_dir, affected_months, on_log)
