# Copyright (c) 2026 rkwithb (https://github.com/rkwithb)
# Licensed under CC BY-NC 4.0 (Non-Commercial Use Only)
# Disclaimer: Use at your own risk. The author is not responsible for any damages.

"""
core/server.py

Flask local server for plurk-fav full-mode viewer.
Serves index.html and static assets, and provides API endpoints for
cross-month search, filtering, sorting, and tag management.

Server lifecycle (managed by GUI):
    start_server(conn, port)  → starts Flask in a daemon thread
    stop_server()             → signals the server to shut down
    wait_until_ready(port)    → polls /health until 200 or timeout

API endpoints:
    GET  /              → serve index.html
    GET  /health        → server ready check (returns {"status": "ok"})
    GET  /api/plurks    → query plurks with optional filters and sort
    GET  /api/tags      → list all tags
    POST /api/tags      → add a tag to a plurk
    DELETE /api/tags    → remove a tag from a plurk

/api/plurks query parameters:
    month=YYYY_MM       filter by month (omit for cross-month)
    nick_name=str       filter by post owner nick name
    plurk_type=int      filter by type (0=public, 1=private, 4=anonymous)
    sort=str            sort order: "newest" (default, only supported value in v1)

Tag request body (JSON):
    {"plurk_id": 123, "tag_name": "my tag"}

core/ hard rule: server.py imports from core/db.py only, never from ui/.
"""

import json
import sqlite3
import threading
import time
from typing import Optional

import requests
from flask import Flask, Response, jsonify, request, send_from_directory

from core.logger import get_logger
from core.paths import BACKUP_DIR, BASE_DIR, INDEX_PATH, STYLE_PATH

logger = get_logger()

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

# The single Flask app instance for this process
_app: Optional[Flask] = None

# The server thread — daemon so it dies automatically if the main process exits
_server_thread: Optional[threading.Thread] = None

# Shared connection — set by start_server(), used by all request handlers
_conn: Optional[sqlite3.Connection] = None

# Active port — set by start_server(), used by stop_server() and wait_until_ready()
_port: int = 5123

# Allowed sort column names — whitelist to prevent SQL injection.
# response_count and favorite_count deferred to v2.
_SORT_COLUMNS = {
    "newest": "f.plurk_id DESC",
}


# ---------------------------------------------------------------------------
# Flask app factory
# ---------------------------------------------------------------------------

def _create_app() -> Flask:
    """
    Create and configure the Flask application with all routes registered.
    Called once by start_server().
    """
    app = Flask(__name__, static_folder=None)
    app.config["JSON_AS_ASCII"] = False

    # -- Static file serving ---------------------------------------------

    @app.route("/")
    def serve_index():
        """Serve index.html from BASE_DIR."""
        return send_from_directory(str(BASE_DIR), "index.html")

    @app.route("/style.css")
    def serve_style():
        """Serve style.css from BASE_DIR."""
        return send_from_directory(str(BASE_DIR), "style.css")

    @app.route("/backup_js/<path:filename>")
    def serve_backup_js(filename: str):
        """Serve JS files from backup_js/."""
        return send_from_directory(str(BACKUP_DIR), filename)

    # -- Health check ----------------------------------------------------

    @app.route("/health")
    def health():
        """Server ready check. Polled by the GUI before opening the browser."""
        return jsonify({"status": "ok"})

    # -- Plurks API ------------------------------------------------------

    @app.route("/api/plurks")
    def api_plurks():
        """
        Query plurks with optional filtering and sorting.

        Query params:
            month=YYYY_MM       (optional) filter by month
            nick_name=str       (optional) filter by owner nick name
            plurk_type=int      (optional) filter by plurk type
            sort=str            (optional) sort order key — "newest" only in v1 (default)
        """
        month      = request.args.get("month",      "").strip()
        nick_name  = request.args.get("nick_name",  "").strip()
        plurk_type = request.args.get("plurk_type", "").strip()
        sort_key   = request.args.get("sort",       "newest").strip()

        order_by = _SORT_COLUMNS.get(sort_key, _SORT_COLUMNS["newest"])

        conditions = []
        params     = []

        if month:
            # Convert YYYY_MM to ISO prefix for posted2 LIKE filter
            iso_prefix = month.replace("_", "-")
            conditions.append("f.posted2 LIKE ?")
            params.append(f"{iso_prefix}%")

        if nick_name:
            conditions.append("f.nick_name = ?")
            params.append(nick_name)

        if plurk_type != "":
            try:
                conditions.append("f.plurk_type = ?")
                params.append(int(plurk_type))
            except ValueError:
                return jsonify({"error": "invalid plurk_type"}), 400

        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        sql = f"""
            SELECT f.plurk_id, f.raw_json,
                   GROUP_CONCAT(t.name) AS tags
            FROM favorites f
            LEFT JOIN plurk_tags pt ON f.plurk_id = pt.plurk_id
            LEFT JOIN tags t        ON pt.tag_id   = t.id
            {where_clause}
            GROUP BY f.plurk_id
            ORDER BY {order_by}
        """

        try:
            cursor = _conn.cursor()
            cursor.row_factory = sqlite3.Row
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        except Exception as e:
            logger.error("server: /api/plurks query failed — %s", e)
            return jsonify({"error": "query failed"}), 500

        plurks = _build_plurk_list(rows)
        return jsonify({"plurks": plurks})

    # -- Tags API --------------------------------------------------------

    @app.route("/api/tags", methods=["GET"])
    def api_tags_list():
        """List all tags in the database."""
        try:
            cursor = _conn.cursor()
            cursor.execute("SELECT id, name FROM tags ORDER BY name")
            tags = [{"id": row[0], "name": row[1]} for row in cursor.fetchall()]
            return jsonify({"tags": tags})
        except Exception as e:
            logger.error("server: /api/tags GET failed — %s", e)
            return jsonify({"error": "query failed"}), 500

    @app.route("/api/tags", methods=["POST"])
    def api_tags_add():
        """
        Add a tag to a plurk. Creates the tag if it does not exist.

        Request body (JSON):
            {"plurk_id": 123, "tag_name": "my tag"}
        """
        body = request.get_json(silent=True)
        if not body:
            return jsonify({"error": "missing JSON body"}), 400

        plurk_id = body.get("plurk_id")
        tag_name = str(body.get("tag_name", "")).strip()

        if not plurk_id or not tag_name:
            return jsonify({"error": "plurk_id and tag_name are required"}), 400

        try:
            cursor = _conn.cursor()

            # Insert tag if not exists, get its id
            cursor.execute(
                "INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag_name,)
            )
            cursor.execute("SELECT id FROM tags WHERE name = ?", (tag_name,))
            tag_id = cursor.fetchone()[0]

            # Link tag to plurk (ignore if already linked)
            cursor.execute(
                "INSERT OR IGNORE INTO plurk_tags (plurk_id, tag_id) VALUES (?, ?)",
                (plurk_id, tag_id),
            )
            _conn.commit()
            logger.debug("server: tag '%s' added to plurk_id=%s", tag_name, plurk_id)
            return jsonify({"ok": True, "tag_id": tag_id})

        except Exception as e:
            logger.error("server: /api/tags POST failed — %s", e)
            return jsonify({"error": "operation failed"}), 500

    @app.route("/api/tags", methods=["DELETE"])
    def api_tags_remove():
        """
        Remove a tag from a plurk.

        Request body (JSON):
            {"plurk_id": 123, "tag_name": "my tag"}
        """
        body = request.get_json(silent=True)
        if not body:
            return jsonify({"error": "missing JSON body"}), 400

        plurk_id = body.get("plurk_id")
        tag_name = str(body.get("tag_name", "")).strip()

        if not plurk_id or not tag_name:
            return jsonify({"error": "plurk_id and tag_name are required"}), 400

        try:
            cursor = _conn.cursor()
            cursor.execute("SELECT id FROM tags WHERE name = ?", (tag_name,))
            row = cursor.fetchone()
            if not row:
                return jsonify({"error": "tag not found"}), 404

            tag_id = row[0]
            cursor.execute(
                "DELETE FROM plurk_tags WHERE plurk_id = ? AND tag_id = ?",
                (plurk_id, tag_id),
            )
            _conn.commit()
            logger.debug("server: tag '%s' removed from plurk_id=%s", tag_name, plurk_id)
            return jsonify({"ok": True})

        except Exception as e:
            logger.error("server: /api/tags DELETE failed — %s", e)
            return jsonify({"error": "operation failed"}), 500

    return app


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_plurk_list(rows) -> list:
    """
    Build the trimmed plurk list from DB rows for API responses.
    Mirrors the JS object shape produced by export.py so index.html
    can handle both sources identically.
    """
    from core.export import _JS_FIELDS, base36_encode

    plurks = []
    for row in rows:
        try:
            raw      = json.loads(row["raw_json"])
            plurk_id = row["plurk_id"]
            tags_str = row["tags"]

            obj = {field: raw.get(field) for field in _JS_FIELDS}
            obj["plurk_url"] = f"https://www.plurk.com/p/{base36_encode(plurk_id)}"
            obj["tags"]      = tags_str.split(",") if tags_str else []

            plurks.append(obj)
        except Exception as e:
            logger.warning("server: skipping malformed row plurk_id=%s — %s", row["plurk_id"], e)

    return plurks


# ---------------------------------------------------------------------------
# Public API — server lifecycle
# ---------------------------------------------------------------------------

def start_server(conn: sqlite3.Connection, port: int) -> None:
    """
    Start the Flask server in a background daemon thread.
    Safe to call only once per process — subsequent calls are ignored
    if the server is already running.

    Args:
        conn: open database connection shared with the GUI
        port: port to listen on (from config, default 5123)
    """
    global _app, _server_thread, _conn, _port

    if _server_thread and _server_thread.is_alive():
        logger.debug("server: already running — ignoring start_server() call")
        return

    _conn = conn
    _port = port
    _app  = _create_app()

    def _run():
        # Disable Flask's default request logger to keep our log area clean
        import logging as _logging
        _logging.getLogger("werkzeug").setLevel(_logging.ERROR)
        _app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)

    _server_thread = threading.Thread(target=_run, daemon=True, name="flask-server")
    _server_thread.start()
    logger.info("server: started on port %d", port)


def stop_server() -> None:
    """
    Signal the Flask server to shut down.
    Flask's built-in shutdown is only available from within a request context,
    so we use werkzeug's shutdown mechanism via a one-shot internal request.
    The daemon thread will also die automatically when the main process exits.
    """
    global _server_thread

    if not _server_thread or not _server_thread.is_alive():
        logger.debug("server: not running — ignoring stop_server() call")
        return

    try:
        # Register a temporary shutdown route and hit it once
        if _app:
            @_app.route("/_shutdown", methods=["POST"])
            def _shutdown():
                func = request.environ.get("werkzeug.server.shutdown")
                if func:
                    func()
                return "bye"

            requests.post(f"http://127.0.0.1:{_port}/_shutdown", timeout=2)
    except Exception:
        # If the request fails the daemon thread will clean up on process exit
        pass

    logger.info("server: shutdown requested")


def wait_until_ready(port: int, timeout: float = 10.0) -> bool:
    """
    Poll GET /health until the server responds with 200, or until timeout.
    Called by the GUI between start_server() and webbrowser.open().

    Args:
        port:    port the server is listening on
        timeout: maximum seconds to wait (default 10)

    Returns:
        True if the server became ready within the timeout, False otherwise.
    """
    url      = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        try:
            resp = requests.get(url, timeout=1)
            if resp.status_code == 200:
                logger.debug("server: ready on port %d", port)
                return True
        except Exception:
            pass
        time.sleep(0.2)

    logger.warning("server: did not become ready within %.1fs", timeout)
    return False
