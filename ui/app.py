# Copyright (c) 2026 rkwithb (https://github.com/rkwithb)
# Licensed under CC BY-NC 4.0 (Non-Commercial Use Only)
# Disclaimer: Use at your own risk. The author is not responsible for any damages.

import customtkinter as ctk
import os
import subprocess
import sys
import threading
import traceback
import webbrowser
from pathlib import Path

# Ensure project root is in sys.path so 'core' package can be found
# regardless of which directory the script is launched from
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.auth import get_keys, save_keys, build_plurk_client, start_oauth, finish_oauth
from core.backup import run_backup_task
from core.config import load_config, save_config
from core.db import init_db, get_total_count
from core.i18n import load_language, get_language, t, SUPPORTED_LANGUAGES
from core.logger import setup_logger, get_logger, shutdown_logger, _get_existing_log_path
from core.paths import BACKUP_DIR, DB_PATH, INDEX_PATH, ensure_backup_dir
from core.server import start_server, wait_until_ready

# Version is generated at build time by CI (core/version.py).
# Falls back to "dev" in local source runs where the file does not exist.
try:
    from core.version import VERSION
except ImportError:
    VERSION = "dev"

# ==========================================
# Theme & Appearance
# ==========================================
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ==========================================
# Colour palette — dark theme
# ==========================================
CLR_BG           = "#000000"   # main background
CLR_PANEL        = "#1a1a1a"   # subtle panel background
CLR_ACCENT       = "#ffffff"   # primary text / accent (light on dark)
CLR_ACCENT2      = "#818cf8"   # blue accent
CLR_TEXT         = "#ffffff"   # primary text
CLR_SUBTEXT      = "#cccccc"   # secondary / hint text
CLR_SUCCESS      = "#16a34a"   # success green
CLR_WARN         = "#d97706"   # warning amber
CLR_ERROR        = "#dc2626"   # error red
CLR_BORDER       = "#ffffff"   # nav-style border
CLR_DIVIDER      = "#ffffff"   # stat row divider lines
CLR_ENTRY_BORDER = "#555555"
CLR_BTN_PRIMARY  = "#64748b"   # primary action button background
CLR_BTN_HOVER    = "#333333"   # primary action button hover


class StatCard(ctk.CTkFrame):
    """
    Stat display card with subtle background and rounded corners.
    Displays a large numeric value and a small label beneath it.
    """

    def __init__(self, master, label: str, color: str, **kwargs):
        super().__init__(master, fg_color=CLR_PANEL, corner_radius=10, **kwargs)

        self._var = ctk.StringVar(value="0")

        ctk.CTkLabel(
            self, textvariable=self._var,
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color=color,
        ).pack(pady=(10, 0))

        ctk.CTkLabel(
            self, text=label,
            font=ctk.CTkFont(size=11),
            text_color=CLR_SUBTEXT,
        ).pack(pady=(0, 10))

    def set(self, value: str):
        """Update the displayed value (accepts string for DB size display)."""
        self._var.set(str(value))


class App(ctk.CTk):
    def __init__(self, cfg: "AppConfig", cleanup_msg: str | None = None):
        super().__init__()

        self.title(t("header_title"))
        self.geometry("760x780")
        self.minsize(680, 680)
        self.configure(fg_color=CLR_BG)

        # Track whether a backup run is currently in progress.
        # Set True when worker thread starts, False when _on_done() is called.
        # Used by _on_closing() to decide whether to show the confirmation dialog.
        self._running: bool = False

        # Stop event — set when user closes window during an active backup.
        # Shared with run_backup_task() via the on_stop callback pattern.
        self._stop_event = threading.Event()

        # Persisted config — passed in from main() to avoid a second file read.
        # Mutated in place, written back on change.
        self._cfg = cfg

        # Open DB connection — kept open for the lifetime of the app.
        ensure_backup_dir()
        self._conn = init_db(str(DB_PATH), on_log=lambda msg: self._append_log(msg))

        # Flask server started flag — prevents double-start on repeated clicks.
        self._server_started: bool = False

        # Logger already initialised in main() before App is constructed
        self._logger   = get_logger()
        self._log_path = _get_existing_log_path(self._logger)
        self._logger.info("App initialised — language=%s UI starting up", get_language())

        # Register exception hooks before building UI so any init error is captured
        self._register_exception_hooks()

        self._build_ui()

        # Load API keys from tool.env into the setup panel fields on launch
        self._load_keys_to_fields()

        # Check for missing viewer files and warn in log area
        self._check_viewer_files()

        # Show initial stats
        self._refresh_stats()

        # Show log retention message if old session files were deleted at this launch
        if cleanup_msg:
            self._append_log(cleanup_msg)

        # Hook window close button to our controlled shutdown handler
        self.protocol("WM_DELETE_WINDOW", self._on_closing)

        self._logger.info("UI ready — log file: %s", self._log_path)

    # ------------------------------------------------------------------
    # Exception hooks — catch unhandled errors in all threads
    # ------------------------------------------------------------------

    def _register_exception_hooks(self):
        """
        Register global exception handlers for both the main thread and
        any background worker threads.

        sys.excepthook:
            Called when an unhandled exception reaches the top of the main thread.
            Logs the full traceback, then lets Python exit normally.

        threading.excepthook:
            Called when an unhandled exception occurs inside any Thread.
            Logs the full traceback and resets UI state so the Start button
            does not stay stuck in running state forever.
        """
        def _main_excepthook(exc_type, exc_value, exc_tb):
            tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
            self._logger.critical("Unhandled exception in main thread:\n%s", tb_text)
            shutdown_logger(reason="exception")
            sys.__excepthook__(exc_type, exc_value, exc_tb)

        def _thread_excepthook(args):
            tb_text = "".join(
                traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
            )
            thread_name = args.thread.name if args.thread else "unknown"
            self._logger.critical(
                "Unhandled exception in thread '%s':\n%s", thread_name, tb_text
            )
            # Reset UI on the main thread — worker died without calling _on_done()
            self.after(0, self._on_worker_crash)

        sys.excepthook       = _main_excepthook
        threading.excepthook = _thread_excepthook

    def _on_worker_crash(self):
        """
        Called on the main thread when the worker thread died unexpectedly.
        Resets UI to a recoverable state so the user can try again.
        """
        self._running = False
        self._start_btn.configure(state="normal", text=t("btn_start_backup"))
        self._mode_dropdown.configure(state="normal")
        self._append_log("")
        self._append_log(t("log_worker_crash"))
        self._logger.error("Worker thread crashed — UI reset to idle state")

    # ------------------------------------------------------------------
    # Window close handler
    # ------------------------------------------------------------------

    def _on_closing(self):
        """
        Called when the user clicks the window close button (X).

        If no backup is running:
            Log session end and close immediately.

        If a backup is running:
            Show a confirmation dialog. If user confirms, set stop_event,
            log the interruption and close. If user cancels, do nothing.
        """
        if not self._running:
            self._logger.info("User closed the window — no active run")
            self._conn.close()
            shutdown_logger(reason="user_closed")
            self.destroy()
            return

        self._show_close_confirm_dialog()

    def _show_close_confirm_dialog(self):
        """
        Display an on-theme CTkToplevel confirmation dialog when the user
        tries to close the window during an active backup run.
        Blocks interaction with the main window until dismissed.
        """
        dialog = ctk.CTkToplevel(self)
        dialog.title(t("dialog_title_confirm_quit"))
        dialog.geometry("360x160")
        dialog.resizable(False, False)
        dialog.configure(fg_color=CLR_PANEL)
        dialog.transient(self)
        dialog.grab_set()

        ctk.CTkLabel(
            dialog,
            text=t("dialog_msg_backup_running"),
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=CLR_TEXT,
        ).pack(pady=(28, 4))

        ctk.CTkLabel(
            dialog,
            text=t("dialog_msg_quit_warning"),
            font=ctk.CTkFont(size=12),
            text_color=CLR_SUBTEXT,
        ).pack(pady=(0, 20))

        btn_row = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_row.pack()

        def _confirm():
            self._logger.warning(
                "User closed the window during an active backup run — session interrupted"
            )
            self._stop_event.set()
            self._conn.close()
            shutdown_logger(reason="interrupted")
            dialog.destroy()
            self.destroy()

        def _cancel():
            self._logger.info("User dismissed close dialog — backup continuing")
            dialog.destroy()

        ctk.CTkButton(
            btn_row,
            text=t("btn_confirm_quit"),
            width=120, height=36,
            fg_color=CLR_ERROR,
            hover_color="#b91c1c",
            text_color="#ffffff",
            font=ctk.CTkFont(size=13),
            command=_confirm,
        ).pack(side="left", padx=(0, 12))

        ctk.CTkButton(
            btn_row,
            text=t("btn_continue_backup"),
            width=120, height=36,
            fg_color=CLR_BTN_PRIMARY,
            hover_color=CLR_BTN_HOVER,
            text_color="#ffffff",
            font=ctk.CTkFont(size=13),
            command=_cancel,
        ).pack(side="left")

    # ------------------------------------------------------------------
    # Language switcher
    # ------------------------------------------------------------------

    def _on_language_change(self, display_label: str):
        """
        Called when the user selects a new language from the dropdown.
        Saves the selection to config.json and restarts the app to apply.

        When frozen (PyInstaller --onefile), PYINSTALLER_RESET_ENVIRONMENT=1
        must be set in the child environment so PyInstaller treats the new
        process as a fresh top-level launch rather than inheriting the parent's
        _MEI temp directory context, which causes Tcl/Tk initialisation to fail.
        """
        selected_lang = next(
            (code for code, label in SUPPORTED_LANGUAGES.items() if label == display_label),
            None,
        )

        if selected_lang is None or selected_lang == get_language():
            return

        self._logger.info("Language changed to '%s' — restarting app", selected_lang)

        # Mutate the live config in place — preserves port value set in this session
        self._cfg.language = selected_lang
        save_config(self._cfg)
        shutdown_logger(reason="language_change")

        # Frozen binary: set PYINSTALLER_RESET_ENVIRONMENT so the child process
        # bootstraps cleanly without inheriting the parent's _MEI context.
        # Source mode: pass sys.argv so the correct script path is included.
        if getattr(sys, "frozen", False):
            env = os.environ.copy()
            env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
            subprocess.Popen([sys.executable], env=env)
        else:
            subprocess.Popen([sys.executable] + sys.argv)

        self.destroy()

    # ------------------------------------------------------------------
    # Setup panel collapse / expand
    # ------------------------------------------------------------------

    def _toggle_setup(self):
        """
        Toggle the setup panel content frame between visible and hidden.
        Updates the toggle button label to reflect current state.
        Called by the [+]/[-] button in the setup panel header.
        """
        if self._setup_content.winfo_ismapped():
            self._setup_content.grid_remove()
            self._setup_toggle_btn.configure(text="[+]")
            self._logger.debug("Setup panel collapsed")
        else:
            self._setup_content.grid()
            self._setup_toggle_btn.configure(text="[-]")
            self._logger.debug("Setup panel expanded")

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)  # log area expands

        # ── Header ──────────────────────────────────────────────────
        header = ctk.CTkFrame(self, fg_color=CLR_PANEL, corner_radius=0, height=64)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)

        ctk.CTkLabel(
            header,
            text=f"  {t('header_title')}",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=CLR_TEXT,
        ).grid(row=0, column=0, pady=16, padx=24, sticky="w")

        # Subtitle — fixed English, not translated
        ctk.CTkLabel(
            header,
            text="Plurk Favorites Archive",
            font=ctk.CTkFont(family="monospace", size=14),
            text_color=CLR_SUBTEXT,
        ).grid(row=0, column=1, pady=16, padx=8, sticky="w")

        # Language dropdown — right side of header
        lang_options   = list(SUPPORTED_LANGUAGES.values())
        current_label  = SUPPORTED_LANGUAGES.get(get_language(), lang_options[0])

        self._lang_dropdown = ctk.CTkOptionMenu(
            header,
            values=lang_options,
            command=self._on_language_change,
            fg_color=CLR_PANEL,
            button_color=CLR_BTN_PRIMARY,
            button_hover_color=CLR_BTN_HOVER,
            text_color=CLR_TEXT,
            font=ctk.CTkFont(size=12),
            width=110,
            height=30,
        )
        self._lang_dropdown.set(current_label)
        self._lang_dropdown.grid(row=0, column=2, pady=16, padx=24, sticky="e")

        # ── Setup Panel ──────────────────────────────────────────────
        setup = ctk.CTkFrame(self, fg_color=CLR_PANEL, corner_radius=12)
        setup.grid(row=1, column=0, sticky="ew", padx=20, pady=(16, 0))
        setup.columnconfigure(1, weight=1)

        # Header row — always visible, contains toggle button
        setup_header = ctk.CTkFrame(setup, fg_color="transparent")
        setup_header.grid(row=0, column=0, columnspan=3, sticky="ew", padx=16, pady=(10, 0))
        setup_header.columnconfigure(0, weight=1)

        ctk.CTkLabel(
            setup_header,
            text=t("setup_title"),
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=CLR_ACCENT,
        ).grid(row=0, column=0, sticky="w")

        self._setup_toggle_btn = ctk.CTkButton(
            setup_header,
            text="[+]",
            width=36, height=26,
            fg_color="transparent",
            hover_color=CLR_BTN_HOVER,
            border_color=CLR_ENTRY_BORDER,
            border_width=1,
            text_color=CLR_SUBTEXT,
            font=ctk.CTkFont(family="monospace", size=12),
            command=self._toggle_setup,
        )
        self._setup_toggle_btn.grid(row=0, column=1, sticky="e")

        # Content frame — shown/hidden by _toggle_setup()
        self._setup_content = ctk.CTkFrame(setup, fg_color="transparent")
        self._setup_content.grid(row=1, column=0, columnspan=3, sticky="ew")
        self._setup_content.columnconfigure(1, weight=1)

        # Four masked key entry rows — parented to _setup_content
        self._ck_entry  = self._make_key_row(self._setup_content, t("label_consumer_key"),    row=0)
        self._cs_entry  = self._make_key_row(self._setup_content, t("label_consumer_secret"), row=1)
        self._at_entry  = self._make_key_row(self._setup_content, t("label_access_token"),    row=2)
        self._ats_entry = self._make_key_row(self._setup_content, t("label_token_secret"),    row=3)

        # Save Keys + Authorize buttons
        btn_row = ctk.CTkFrame(self._setup_content, fg_color="transparent")
        btn_row.grid(row=4, column=0, columnspan=3, sticky="ew", padx=16, pady=(8, 14))

        ctk.CTkButton(
            btn_row,
            text=t("btn_save_keys"),
            width=140, height=34,
            fg_color=CLR_BTN_PRIMARY,
            hover_color=CLR_BTN_HOVER,
            text_color=CLR_TEXT,
            font=ctk.CTkFont(size=13),
            command=self._on_save_keys,
        ).pack(side="left", padx=(0, 12))

        ctk.CTkButton(
            btn_row,
            text=t("btn_authorize"),
            width=140, height=34,
            fg_color=CLR_BTN_PRIMARY,
            hover_color=CLR_BTN_HOVER,
            text_color=CLR_TEXT,
            font=ctk.CTkFont(size=13),
            command=self._on_authorize,
        ).pack(side="left")

        # ── Backup Panel ─────────────────────────────────────────────
        backup = ctk.CTkFrame(self, fg_color=CLR_PANEL, corner_radius=12)
        backup.grid(row=2, column=0, sticky="ew", padx=20, pady=(12, 0))
        backup.columnconfigure(0, weight=1)

        ctk.CTkLabel(
            backup, text=t("backup_title"),
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=CLR_ACCENT,
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 6))

        # Mode selector state — default: incremental
        self._active_mode: str = "incremental"

        # Map display label → internal mode key.
        # Built here so t() is resolved at widget-construction time.
        self._mode_label_map: dict[str, str] = {
            t("mode_incremental"): "incremental",
            t("mode_by_date"):     "date",
            t("mode_full"):        "full",
        }

        # Row 1 — mode selector row: label + dropdown
        mode_row = ctk.CTkFrame(backup, fg_color="transparent")
        mode_row.grid(row=1, column=0, sticky="w", padx=16, pady=(0, 6))

        ctk.CTkLabel(
            mode_row,
            text=t("label_backup_mode"),
            font=ctk.CTkFont(size=12),
            text_color=CLR_SUBTEXT,
        ).pack(side="left", padx=(0, 8))

        self._mode_dropdown = ctk.CTkOptionMenu(
            mode_row,
            values=list(self._mode_label_map.keys()),
            command=self._on_mode_select,
            fg_color=CLR_BTN_PRIMARY,
            button_color=CLR_BTN_HOVER,
            button_hover_color=CLR_BTN_HOVER,
            text_color=CLR_TEXT,
            font=ctk.CTkFont(size=13),
            width=160,
            height=32,
        )
        self._mode_dropdown.set(t("mode_incremental"))
        self._mode_dropdown.pack(side="left")

        # Date entry and hint — parented to mode_row, packed to the right of the dropdown.
        # Hidden on init via pack_forget(); shown/hidden by _set_mode() when date mode toggles.
        self._date_entry = ctk.CTkEntry(
            mode_row,
            width=120, height=32,
            fg_color=CLR_BG,
            border_color=CLR_ENTRY_BORDER,
            text_color=CLR_TEXT,
            font=ctk.CTkFont(size=13),
        )
        self._date_entry.bind("<KeyRelease>", self._on_date_change)
        # Not packed on init — _set_mode() calls pack()/pack_forget() to toggle visibility

        self._date_hint = ctk.CTkLabel(
            mode_row,
            text=t("date_hint"),
            font=ctk.CTkFont(size=11),
            text_color=CLR_SUBTEXT,
        )
        # Not packed on init — shown alongside _date_entry in date mode only

        # Row 3 — [Start Backup] full width
        self._start_btn = ctk.CTkButton(
            backup,
            text=t("btn_start_backup"),
            height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=CLR_BTN_PRIMARY,
            hover_color=CLR_BTN_HOVER,
            text_color="#ffffff",
            corner_radius=8,
            command=self._on_start,
        )
        self._start_btn.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 14))
        # ── Stats Bar ────────────────────────────────────────────────
        stats_wrapper = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        stats_wrapper.grid(row=3, column=0, sticky="ew", padx=0, pady=0)
        stats_wrapper.columnconfigure(0, weight=1)

        ctk.CTkFrame(
            stats_wrapper, fg_color=CLR_DIVIDER, height=1, corner_radius=0
        ).grid(row=0, column=0, sticky="ew")

        stats_row = ctk.CTkFrame(stats_wrapper, fg_color="transparent", corner_radius=0)
        stats_row.grid(row=1, column=0, sticky="ew")
        for i in range(3):
            stats_row.columnconfigure(i, weight=1)

        self._card_total    = StatCard(stats_row, t("stats_total_saved"), CLR_SUCCESS)
        self._card_this_run = StatCard(stats_row, t("stats_this_run"),    CLR_ACCENT2)
        self._card_db_size  = StatCard(stats_row, t("stats_db_size"),     CLR_SUBTEXT)

        self._card_total.grid(row=0, column=0,    sticky="ew", padx=(0, 6))
        self._card_this_run.grid(row=0, column=1, sticky="ew", padx=3)
        self._card_db_size.grid(row=0, column=2,  sticky="ew", padx=(6, 0))

        ctk.CTkFrame(
            stats_wrapper, fg_color=CLR_DIVIDER, height=1, corner_radius=0
        ).grid(row=2, column=0, sticky="ew")

        # ── Log Area ─────────────────────────────────────────────────
        log_frame = ctk.CTkFrame(self, fg_color=CLR_PANEL, corner_radius=12)
        log_frame.grid(row=4, column=0, sticky="nsew", padx=20, pady=(12, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(1, weight=1)
        self.rowconfigure(4, weight=1)  # log frame row expands

        log_header = ctk.CTkFrame(log_frame, fg_color="transparent")
        log_header.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        log_header.columnconfigure(0, weight=1)

        ctk.CTkLabel(
            log_header,
            text="LOG",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=CLR_ACCENT,
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkButton(
            log_header,
            text=t("btn_clear_log"),
            width=60, height=26,
            fg_color="transparent",
            hover_color=CLR_BTN_HOVER,
            border_color=CLR_ENTRY_BORDER,
            border_width=1,
            text_color=CLR_SUBTEXT,
            font=ctk.CTkFont(size=11),
            command=self._clear_log,
        ).grid(row=0, column=1, sticky="e")

        self._log_box = ctk.CTkTextbox(
            log_frame,
            font=ctk.CTkFont(family="monospace", size=11),
            fg_color=CLR_BG,
            text_color=CLR_TEXT,
            border_color=CLR_ENTRY_BORDER,
            border_width=1,
            wrap="word",
            state="disabled",
        )
        self._log_box.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))

        # ── Bottom Bar ───────────────────────────────────────────────
        bottom = ctk.CTkFrame(self, fg_color=CLR_PANEL, corner_radius=0)
        bottom.grid(row=5, column=0, sticky="ew", pady=(12, 0))
        bottom.columnconfigure(3, weight=1)

        ctk.CTkButton(
            bottom,
            text=t("btn_open_index"),
            height=36,
            fg_color="transparent",
            hover_color=CLR_BTN_HOVER,
            border_color=CLR_BORDER,
            border_width=1,
            text_color=CLR_TEXT,
            font=ctk.CTkFont(size=12),
            command=self._open_index,
        ).grid(row=0, column=0, padx=(16, 4), pady=10)

        ctk.CTkButton(
            bottom,
            text=t("btn_open_viewer"),
            height=36,
            fg_color="transparent",
            hover_color=CLR_BTN_HOVER,
            border_color=CLR_BORDER,
            border_width=1,
            text_color=CLR_TEXT,
            font=ctk.CTkFont(size=12),
            command=self._open_viewer,
        ).grid(row=0, column=1, padx=4, pady=10)

        ctk.CTkButton(
            bottom,
            text=t("btn_open_backup_dir"),
            height=36,
            fg_color="transparent",
            hover_color=CLR_BTN_HOVER,
            border_color=CLR_BORDER,
            border_width=1,
            text_color=CLR_TEXT,
            font=ctk.CTkFont(size=12),
            command=self._open_backup_dir,
        ).grid(row=0, column=2, padx=4, pady=10)

        # Port field — right side of bottom bar
        port_frame = ctk.CTkFrame(bottom, fg_color="transparent")
        port_frame.grid(row=0, column=3, sticky="e", padx=16, pady=10)

        ctk.CTkLabel(
            port_frame,
            text="Port",
            font=ctk.CTkFont(size=12),
            text_color=CLR_SUBTEXT,
        ).pack(side="left", padx=(0, 6))

        self._port_var = ctk.StringVar(value=str(self._cfg.port))
        self._port_entry = ctk.CTkEntry(
            port_frame,
            textvariable=self._port_var,
            width=64, height=32,
            fg_color=CLR_BG,
            border_color=CLR_ENTRY_BORDER,
            text_color=CLR_TEXT,
            font=ctk.CTkFont(size=12),
        )
        self._port_entry.pack(side="left")
        self._port_var.trace_add("write", self._on_port_change)

    # ------------------------------------------------------------------
    # Key row helper
    # ------------------------------------------------------------------

    def _make_key_row(self, parent, label: str, row: int) -> ctk.CTkEntry:
        """
        Build a single API key row: label + masked entry + show/hide toggle.
        Returns the CTkEntry so the caller can read/write its value.
        """
        ctk.CTkLabel(
            parent,
            text=label,
            font=ctk.CTkFont(size=12),
            text_color=CLR_SUBTEXT,
            width=140, anchor="w",
        ).grid(row=row, column=0, padx=(16, 8), pady=3, sticky="w")

        entry = ctk.CTkEntry(
            parent,
            show="*",
            fg_color=CLR_BG,
            border_color=CLR_ENTRY_BORDER,
            text_color=CLR_TEXT,
            font=ctk.CTkFont(size=13),
            height=32,
        )
        entry.grid(row=row, column=1, sticky="ew", padx=(0, 8), pady=3)

        # Show/hide toggle button — state captured via closure
        toggle_btn = ctk.CTkButton(
            parent,
            text=t("btn_show"),
            width=60, height=32,
            fg_color="transparent",
            hover_color=CLR_BTN_HOVER,
            border_color=CLR_ENTRY_BORDER,
            border_width=1,
            text_color=CLR_TEXT,
            font=ctk.CTkFont(size=12),
        )
        toggle_btn.grid(row=row, column=2, padx=(0, 16), pady=3)

        def _toggle():
            if entry.cget("show") == "*":
                entry.configure(show="")
                toggle_btn.configure(text=t("btn_hide"))
            else:
                entry.configure(show="*")
                toggle_btn.configure(text=t("btn_show"))

        toggle_btn.configure(command=_toggle)

        return entry

    # ------------------------------------------------------------------
    # Key management
    # ------------------------------------------------------------------

    def _load_keys_to_fields(self):
        """
        Read keys from tool.env and populate the four entry fields.
        Creates tool.env template if missing.
        Collapses setup panel only if all four keys are present.
        Logs actionable guidance for each incomplete state.
        """
        ck, cs, at, ats = get_keys()
        self._ck_entry.delete(0, "end");  self._ck_entry.insert(0, ck)
        self._cs_entry.delete(0, "end");  self._cs_entry.insert(0, cs)
        self._at_entry.delete(0, "end");  self._at_entry.insert(0, at)
        self._ats_entry.delete(0, "end"); self._ats_entry.insert(0, ats)

        if all([ck, cs, at, ats]):
            # All keys present — collapse panel
            self._setup_content.grid_remove()
            self._setup_toggle_btn.configure(text="[+]")
            self._append_log(t("log_keys_loaded"))
            self._logger.info("Keys loaded from tool.env — setup panel collapsed")

        elif ck and cs and not at and not ats:
            # Consumer keys present but OAuth not yet completed —
            # mirror the CLI approach: trigger OAuth automatically on launch
            self._append_log(t("log_keys_loaded"))
            self._append_log(t("log_keys_need_oauth"))
            self._logger.info("Consumer keys loaded — access token missing, triggering OAuth")
            self.after(500, self._on_authorize)  # slight delay so UI is fully ready

        elif not ck and not cs and not at and not ats:
            # Fresh install — tool.env was just created empty by get_keys()
            self._append_log(t("log_keys_env_created"))
            self._logger.info("tool.env created — user needs to fill in Consumer Key/Secret")

    def _on_save_keys(self):
        """Read the four entry fields and persist to tool.env."""
        ck  = self._ck_entry.get().strip()
        cs  = self._cs_entry.get().strip()
        at  = self._at_entry.get().strip()
        ats = self._ats_entry.get().strip()

        if not all([ck, cs, at, ats]):
            self._append_log(t("log_keys_incomplete"))
            self._logger.warning("Save keys — incomplete fields")
            return

        save_keys(ck, cs, at, ats)
        self._append_log(t("log_keys_saved"))
        self._logger.info("Keys saved to tool.env")

    # ------------------------------------------------------------------
    # OAuth flow
    # ------------------------------------------------------------------

    def _on_authorize(self):
        """
        Begin the OAuth handshake in a background thread.
        Opens the browser, then shows a verifier dialog on return.
        """
        ck = self._ck_entry.get().strip()
        cs = self._cs_entry.get().strip()

        if not ck or not cs:
            self._append_log(t("log_keys_incomplete"))
            return

        def _run():
            try:
                client, url = start_oauth(ck, cs)
                self._logger.info("OAuth started — opening browser: %s", url)
                self.after(0, lambda: self._append_log(t("log_auth_opening_browser")))
                webbrowser.open(url)
                # Show verifier dialog on main thread, block until user submits
                self.after(0, lambda: self._show_verifier_dialog(client, ck, cs))
            except Exception as e:
                self._logger.error("OAuth start failed — %s", e, exc_info=True)
                self.after(0, lambda: self._append_log(t("log_auth_failed", error=str(e))))

        threading.Thread(target=_run, daemon=True, name="oauth-start").start()

    def _show_verifier_dialog(self, client, ck: str, cs: str):
        """
        CTkToplevel dialog — user pastes the verifier code obtained from the browser.
        On confirm, calls finish_oauth() in a background thread.
        """
        dialog = ctk.CTkToplevel(self)
        dialog.title(t("dialog_title_oauth"))
        dialog.geometry("400x180")
        dialog.resizable(False, False)
        dialog.configure(fg_color=CLR_PANEL)
        dialog.transient(self)
        dialog.grab_set()

        ctk.CTkLabel(
            dialog,
            text=t("dialog_msg_oauth"),
            font=ctk.CTkFont(size=13),
            text_color=CLR_TEXT,
            wraplength=360,
        ).pack(pady=(20, 8), padx=20)

        verifier_var = ctk.StringVar()
        ctk.CTkEntry(
            dialog,
            textvariable=verifier_var,
            placeholder_text=t("dialog_placeholder_verifier"),
            width=360, height=34,
            fg_color=CLR_BG,
            border_color=CLR_ENTRY_BORDER,
            text_color=CLR_TEXT,
            font=ctk.CTkFont(size=13),
        ).pack(padx=20, pady=(0, 12))

        def _confirm():
            verifier = verifier_var.get().strip()
            if not verifier:
                return
            dialog.destroy()

            def _finish():
                try:
                    at, ats = finish_oauth(client, verifier)
                    save_keys(ck, cs, at, ats)

                    def _update_fields():
                        self._at_entry.delete(0, "end");  self._at_entry.insert(0, at)
                        self._ats_entry.delete(0, "end"); self._ats_entry.insert(0, ats)
                        self._append_log(t("log_auth_success"))

                    self.after(0, _update_fields)
                    self._logger.info("OAuth complete — access token saved")
                except Exception as e:
                    self._logger.error("OAuth finish failed — %s", e)
                    self.after(0, lambda: self._append_log(t("log_auth_failed", error=str(e))))

            threading.Thread(target=_finish, daemon=True, name="oauth-finish").start()

        ctk.CTkButton(
            dialog,
            text=t("btn_confirm_oauth"),
            width=120, height=34,
            fg_color=CLR_BTN_PRIMARY,
            hover_color=CLR_BTN_HOVER,
            text_color="#ffffff",
            font=ctk.CTkFont(size=13),
            command=_confirm,
        ).pack()

    # ------------------------------------------------------------------
    # Backup mode selector
    # ------------------------------------------------------------------
    def _on_mode_select(self, label: str):
        """Called by CTkOptionMenu when the user picks a mode. Resolves display
        label to internal mode key and delegates to _set_mode()."""
        mode = self._mode_label_map.get(label, "incremental")
        self._set_mode(mode)

    def _set_mode(self, mode: str):
        """
        Switch the active backup mode.
        Date entry and hint are enabled/dimmed based on whether date mode is selected.
        Start button state is re-evaluated on every mode switch.
        """
        self._active_mode = mode

        # Show date entry + hint inline when date mode is selected; hide otherwise.
        if mode == "date":
            self._date_entry.pack(side="left", padx=(8, 8))
            self._date_hint.pack(side="left")
            # Re-evaluate Start button — entry may already contain valid content
            self._on_date_change()
        else:
            self._date_entry.pack_forget()
            self._date_hint.pack_forget()
            # Non-date modes: Start always enabled (key validation runs in _on_start)
            self._start_btn.configure(state="normal")

        self._logger.debug("Backup mode changed to '%s'", mode)

    def _on_date_change(self, _event=None):
        """
        KeyRelease callback on _date_entry.
        Enables [Start Backup] only when the entry contains a plausible YYYYMM value:
        exactly 6 digits and month in 01–12.
        Full validation (strptime) runs on click in _launch_backup().
        Only active when date mode is selected; no-op otherwise.
        """
        if self._active_mode != "date":
            return
        value = self._date_entry.get()
        is_plausible = (
            len(value) == 6
            and value.isdigit()
            and 1 <= int(value[4:6]) <= 12
        )
        self._start_btn.configure(state="normal" if is_plausible else "disabled")

    # ------------------------------------------------------------------
    # Backup execution
    # ------------------------------------------------------------------

    def _on_start(self):
        """
        Validate keys and mode, then launch backup worker thread.
        Shows confirmation dialog first if full backup mode is selected.
        """
        ck  = self._ck_entry.get().strip()
        cs  = self._cs_entry.get().strip()
        at  = self._at_entry.get().strip()
        ats = self._ats_entry.get().strip()

        if not all([ck, cs, at, ats]):
            self._append_log(t("log_keys_incomplete"))
            return

        if self._active_mode == "full":
            self._show_full_backup_confirm(lambda: self._launch_backup(ck, cs, at, ats))
            return

        self._launch_backup(ck, cs, at, ats)

    def _show_full_backup_confirm(self, on_confirm):
        """
        CTkToplevel confirmation dialog for full backup mode.
        Warns the user about time cost before proceeding.
        """
        dialog = ctk.CTkToplevel(self)
        dialog.title(t("dialog_title_full_backup"))
        dialog.geometry("420x180")
        dialog.resizable(False, False)
        dialog.configure(fg_color=CLR_PANEL)
        dialog.transient(self)
        dialog.grab_set()

        ctk.CTkLabel(
            dialog,
            text=t("dialog_msg_full_backup"),
            font=ctk.CTkFont(size=13),
            text_color=CLR_TEXT,
            wraplength=380,
        ).pack(pady=(24, 20), padx=20)

        btn_row = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_row.pack()

        def _confirm():
            dialog.destroy()
            on_confirm()

        ctk.CTkButton(
            btn_row,
            text=t("btn_confirm_full"),
            width=120, height=34,
            fg_color=CLR_ERROR,
            hover_color="#b91c1c",
            text_color="#ffffff",
            font=ctk.CTkFont(size=13),
            command=_confirm,
        ).pack(side="left", padx=(0, 12))

        ctk.CTkButton(
            btn_row,
            text=t("btn_cancel"),
            width=120, height=34,
            fg_color=CLR_BTN_PRIMARY,
            hover_color=CLR_BTN_HOVER,
            text_color="#ffffff",
            font=ctk.CTkFont(size=13),
            command=dialog.destroy,
        ).pack(side="left")

    def _launch_backup(self, ck: str, cs: str, at: str, ats: str):
        """
        Resolve mode + criteria, reset UI state, and start the worker thread.
        Called after all validation and confirmation dialogs are complete.
        """
        from datetime import datetime

        # Resolve mode string and criteria value
        if self._active_mode == "incremental":
            mode     = "incremental"
            from core.db import get_last_saved_id
            criteria = get_last_saved_id(self._conn)
        elif self._active_mode == "date":
            mode     = "date"
            date_str = self._date_entry.get().strip()
            try:
                criteria = datetime.strptime(date_str, "%Y%m")
            except ValueError:
                self._append_log(t("log_date_invalid"))
                return
        else:
            mode     = "full"
            criteria = 0

        # Reset stop event for this run
        self._stop_event.clear()

        # Reset UI — disable both start button and mode dropdown during run
        self._start_btn.configure(state="disabled", text=t("btn_running"))
        self._mode_dropdown.configure(state="disabled")
        self._card_this_run.set("0")
        self._running = True

        self._logger.info("--- Backup run started --- mode=%s criteria=%s", mode, criteria)

        def _worker():
            try:
                client = build_plurk_client(ck, cs, at, ats)
                run_backup_task(
                    client      = client,
                    conn        = self._conn,
                    mode        = mode,
                    criteria    = criteria,
                    backup_dir  = str(BACKUP_DIR),
                    stop_event  = self._stop_event,
                    on_log      = self._append_log,
                    on_stats    = self._on_stats,
                )
            except Exception as e:
                self._logger.error("Backup worker error — %s", e)
                self.after(0, lambda: self._append_log(
                    t("log_backup_error", error=str(e))
                ))
            finally:
                self.after(0, self._on_done)

        threading.Thread(target=_worker, daemon=True, name="backup-worker").start()

    def _on_done(self):
        """Called on main thread when backup worker exits (normal or stopped)."""
        self._running = False
        self._start_btn.configure(state="normal", text=t("btn_start_backup"))
        self._mode_dropdown.configure(state="normal")
        self._refresh_stats()
        self._logger.info("--- Backup run ended ---")

    # ------------------------------------------------------------------
    # Stats helpers
    # ------------------------------------------------------------------

    def _on_stats(self, this_run: int, total: int):
        """
        Callback from run_backup_task() — updates stats bar live during backup.
        Called from worker thread; uses after() to update UI safely.
        """
        def _update():
            self._card_total.set(str(total))
            self._card_this_run.set(str(this_run))
        self.after(0, _update)

    def _refresh_stats(self):
        """Read DB for total count and file size, update all three stat cards."""
        try:
            total = get_total_count(self._conn)
            self._card_total.set(str(total))
        except Exception:
            pass

        self._card_this_run.set("0")
        self._update_db_size()

    def _update_db_size(self):
        """Read DB file size from disk and update the DB size stat card."""
        try:
            size_bytes = DB_PATH.stat().st_size
            if size_bytes < 1024 * 1024:
                label = f"{size_bytes // 1024} KB"
            else:
                label = f"{size_bytes / (1024 * 1024):.1f} MB"
            self._card_db_size.set(label)
        except Exception:
            self._card_db_size.set("--")

    # ------------------------------------------------------------------
    # Port change handler
    # ------------------------------------------------------------------

    def _on_port_change(self, *_):
        """Persist port to config whenever the port field changes."""
        try:
            port = int(self._port_var.get())
            if 1024 <= port <= 65535:
                self._cfg.port = port
                save_config(self._cfg)
        except ValueError:
            pass  # User is mid-typing — ignore invalid intermediate values

    # ------------------------------------------------------------------
    # Log helpers
    # ------------------------------------------------------------------

    def _append_log(self, msg: str):
        """
        Append a message to the UI log textbox (thread-safe via after()).
        This is the on_log callback passed to run_backup_task() and init_db().
        File logging is handled separately by each core module.
        """
        def _write():
            self._log_box.configure(state="normal")
            self._log_box.insert("end", msg + "\n")
            self._log_box.see("end")
            self._log_box.configure(state="disabled")
        self.after(0, _write)

    def _clear_log(self):
        """Clear the UI log textbox."""
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")

    # ------------------------------------------------------------------
    # Viewer helpers
    # ------------------------------------------------------------------

    def _check_viewer_files(self):
        """Warn in log area if index.html or style.css are missing."""
        from core.paths import check_viewer_files
        missing = check_viewer_files()
        for name in missing:
            if name == "index.html":
                self._append_log(t("log_viewer_index_missing"))
            else:
                self._append_log(t("log_viewer_style_missing"))
            self._logger.warning("Missing viewer file: %s", name)

    def _open_index(self):
        """Open index.html directly in the browser via file:// (basic mode)."""
        if not INDEX_PATH.exists():
            self._append_log(t("log_viewer_index_missing"))
            return
        webbrowser.open(INDEX_PATH.as_uri())
        self._logger.info("Opened basic viewer: %s", INDEX_PATH)

    def _open_viewer(self):
        """
        Start Flask server (if not already running) then open the browser
        at http://localhost:PORT (full mode with tag support).
        """
        if not self._server_started:
            self._append_log(t("log_viewer_server_starting"))
            try:
                start_server(self._conn, self._cfg.port)
                ready = wait_until_ready(self._cfg.port)
                if not ready:
                    raise RuntimeError("server did not become ready in time")
                self._server_started = True
                self._append_log(t("log_viewer_server_ready"))
                self._logger.info("Flask server started on port %d", self._cfg.port)
            except Exception as e:
                self._append_log(t("log_viewer_server_failed", error=str(e)))
                self._logger.error("Flask server failed to start — %s", e)
                return

        webbrowser.open(f"http://localhost:{self._cfg.port}")
        self._logger.info("Opened full viewer at http://localhost:%d", self._cfg.port)

    def _open_backup_dir(self):
        """Open the backup_js folder in the system file manager."""
        path = str(BACKUP_DIR)
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
            self._logger.info("Opened backup dir: %s", path)
        except Exception as e:
            self._logger.error("Failed to open backup dir — %s", e)


# ==========================================
# Entry point for GUI mode
# ==========================================

def main():
    # Intercept --version before logger/i18n/GUI init — no side effects.
    # Used by CI smoke tests to verify the frozen binary starts cleanly.
    if "--version" in sys.argv:
        print(f"Plurk-Fav v{VERSION}")
        sys.exit(0)

    # Initialise logger first — before i18n, so load_config/load_language
    # warnings are captured in the log file
    log_path, cleanup_msg = setup_logger(mode="GUI")

    # Load persisted language and initialise translations before UI is built
    cfg  = load_config()
    load_language(cfg.language)

    app = App(cfg=cfg, cleanup_msg=cleanup_msg)
    app.mainloop()


if __name__ == "__main__":
    main()
