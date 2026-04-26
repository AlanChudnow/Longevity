"""
gui.py — Main application window (CustomTkinter).
Matches design/Longevity_Risk_Calculator_v2.html.

Layout:
  TOP:    Three input panels side-by-side
  MIDDLE: Calculate button (full width)
  BOTTOM: Results section (placeholder in Phase 4; chart embedded in Phase 5)
"""
from __future__ import annotations

import calendar
import re
import threading
import tkinter as tk
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import customtkinter as ctk
import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _TKDND_AVAILABLE = True
except ImportError:
    _TKDND_AVAILABLE = False

# ---------------------------------------------------------------------------
# Design tokens (from Longevity_Risk_Calculator_v2.html)
# ---------------------------------------------------------------------------
_BG         = "#ffffff"
_SURFACE2   = "#fafbfc"
_SURFACE3   = "#f4f6f8"
_INK1       = "#0f1720"
_INK2       = "#3a4654"
_INK3       = "#6b7a8a"
_INK4       = "#9aa6b2"
_LINE       = "#e7ebef"
_BORDER     = "#9AA6B2"   # visible entry border
_ACCENT     = "#4A6FA5"
_ACCENT_HVR = "#3a5a8a"
_ACCENT_SOFT= "#eef2f8"
_OK         = "#3D9970"
_WARN       = "#E67E22"

_EXPORT_PATH = r"C:\Users\Daddy\Downloads\export.xml"

_WINDOW_OPTS = {
    "None":         "none",
    "Last value":   "last_value",
    "Last 7 days":  "last_week",
    "Last 30 days": "last_month",
}

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

# ---------------------------------------------------------------------------
# Font helpers
# ---------------------------------------------------------------------------
def _font(size=12, weight="normal", mono=False):
    family = "Consolas" if mono else "Segoe UI"
    return ctk.CTkFont(family=family, size=size, weight=weight)


# ---------------------------------------------------------------------------
# Reusable widget helpers
# ---------------------------------------------------------------------------

def _separator(parent, row, col=0, colspan=3, padx=0, pady=0):
    ctk.CTkFrame(
        parent, fg_color=_LINE, height=1, corner_radius=0,
    ).grid(row=row, column=col, columnspan=colspan,
           sticky="ew", padx=padx, pady=pady)


def _panel(parent) -> ctk.CTkFrame:
    return ctk.CTkFrame(
        parent,
        fg_color=_BG,
        border_color=_LINE,
        border_width=1,
        corner_radius=10,
    )


def _panel_header(panel, title: str) -> ctk.CTkFrame:
    """Returns the header frame so callers can add right-side widgets."""
    hdr = ctk.CTkFrame(panel, fg_color=_SURFACE2, corner_radius=0, height=44)
    hdr.grid(row=0, column=0, columnspan=4, sticky="ew")
    hdr.grid_columnconfigure(0, weight=1)
    ctk.CTkLabel(
        hdr, text=title,
        font=_font(13, "bold"), text_color=_INK1,
        anchor="w",
    ).grid(row=0, column=0, sticky="w", padx=14, pady=10)
    _separator(panel, row=1, colspan=4)
    return hdr


# ---------------------------------------------------------------------------
# Main application window
# ---------------------------------------------------------------------------

class LongevityApp(ctk.CTk, TkinterDnD.DnDWrapper if _TKDND_AVAILABLE else object):
    def __init__(self):
        super().__init__()

        # DnD: DnDWrapper mixin + explicit _require on this root window
        self._dnd_ok = False
        if _TKDND_AVAILABLE:
            try:
                self.TkdndVersion = TkinterDnD._require(self)
                self._dnd_ok = True
                print("[DnD] tkinterdnd2 ready.")
            except Exception as exc:
                print(f"[DnD] Init failed, Browse button will be used: {exc}")

        self.title("Longevity Risk Calculator")
        self.geometry("1260x860")
        self.minsize(1020, 720)
        self.configure(fg_color=_BG)

        self._ah_data: Dict = {}
        self._ah_dots:    Dict[str, ctk.CTkLabel] = {}
        self._ah_labels:  Dict[str, ctk.CTkLabel] = {}
        self._ah_vars:    Dict[str, ctk.StringVar] = {}   # backing vars for manual entries
        self._ah_entries: Dict[str, ctk.CTkEntry]  = {}   # editable entries (None mode)
        self._ah_raw:     Dict[str, str]            = {}   # numeric string per field for pre-fill
        self._drop_label = None      # tk.Label inside the DnD zone
        self._lab_extras: Dict[str, Optional[float]] = {}  # crp/hba1c/glucose from parser

        # Fix 3 — window selector state (default "Last value", not "None")
        self._window_var = tk.StringVar(value="Last value")

        # Historical date selector state — default to same month one year ago
        _now_dt = datetime.now()
        self._ah_mode_var = tk.StringVar(value="current")
        self._ah_month_var = tk.StringVar(value=_MONTHS[_now_dt.month - 1])
        self._ah_year_var = tk.StringVar(value=str(_now_dt.year - 1))
        self._ah_month_cb: Optional[ctk.CTkComboBox] = None
        self._ah_year_cb: Optional[ctk.CTkComboBox] = None
        self._chart_shown = False
        self._pending_recalculate = False

        self._var_zip  = tk.StringVar()
        self._zip_info: Dict = {}

        self._build_ui()
        self._on_window_change()   # initial load (default is "Last value")

    # ------------------------------------------------------------------ helpers

    def _entry(self, parent, textvariable,
               placeholder_text: str = "",
               width: int = 96, height: int = 30) -> ctk.CTkEntry:
        """Fix 2 — styled entry with visible border and focus highlight."""
        e = ctk.CTkEntry(
            parent,
            textvariable=textvariable,
            placeholder_text=placeholder_text,
            width=width, height=height,
            font=_font(12),
            border_color=_BORDER,
            border_width=2,
            fg_color=_SURFACE3,
        )
        e.bind("<FocusIn>",  lambda _ev: e.configure(border_color=_ACCENT))
        e.bind("<FocusOut>", lambda _ev: e.configure(border_color=_BORDER))
        return e

    # ------------------------------------------------------------------ UI --

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=0)
        self.grid_rowconfigure(2, weight=1)

        inp = ctk.CTkFrame(self, fg_color=_BG, corner_radius=0)
        inp.grid(row=0, column=0, sticky="nsew", padx=20, pady=(20, 0))
        inp.grid_columnconfigure((0, 1, 2), weight=1, uniform="panel")
        inp.grid_rowconfigure(0, weight=1)

        self._p1 = self._build_panel_ah(inp)
        self._p1.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        self._p2 = self._build_panel_lab(inp)
        self._p2.grid(row=0, column=1, sticky="nsew", padx=6)

        self._p3 = self._build_panel_manual(inp)
        self._p3.grid(row=0, column=2, sticky="nsew", padx=(6, 0))

        _btn_wrap = ctk.CTkFrame(self, fg_color="transparent")
        _btn_wrap.grid(row=1, column=0, sticky="ew", padx=20)
        _btn_wrap.grid_columnconfigure(0, weight=1)

        ctk.CTkButton(
            _btn_wrap, text="CALCULATE",
            height=48, corner_radius=8,
            font=_font(14, "bold"),
            fg_color=_ACCENT, hover_color=_ACCENT_HVR, text_color="#ffffff",
            command=self._on_calculate,
        ).grid(row=0, column=0, sticky="ew", pady=(12, 4))

        self._missing_warn = ctk.CTkLabel(
            _btn_wrap,
            text="",
            font=_font(10), text_color=_WARN, anchor="center",
            wraplength=700,
        )
        self._missing_warn.grid(row=1, column=0, pady=(0, 6))
        self._missing_warn.grid_remove()

        self._results_frame = _panel(self)
        self._results_frame.grid(row=2, column=0, sticky="nsew",
                                  padx=20, pady=(0, 20))
        self._results_frame.grid_columnconfigure(0, weight=1)   # left 1/3
        self._results_frame.grid_columnconfigure(1, weight=2)   # right 2/3 (chart)
        self._results_frame.grid_rowconfigure(2, weight=1)      # risk panel / chart row

        # Placeholder — shown before first CALCULATE
        self._results_placeholder = ctk.CTkLabel(
            self._results_frame,
            text="Click CALCULATE to generate your survival curve.",
            font=_font(13), text_color=_INK4,
        )
        self._results_placeholder.grid(row=0, column=0, rowspan=3, columnspan=2,
                                        padx=20, pady=60)

        # Stats strip (hidden until first calculate)
        self._stats_frame = ctk.CTkFrame(
            self._results_frame, fg_color="transparent",
        )
        self._stats_frame.grid(row=0, column=0, sticky="ew",
                                padx=(16, 4), pady=(12, 4))
        self._stats_frame.grid_columnconfigure((0, 1, 2, 3), weight=1,
                                               uniform="stat")
        self._stats_frame.grid_remove()

        for col, (title, attr) in enumerate([
            ("5-yr Risk",        "_stat_r5"),
            ("10-yr Risk",       "_stat_r10"),
            ("Median Remaining", "_stat_med"),
            ("ASCVD 10-yr",      "_stat_ascvd"),
        ]):
            card = ctk.CTkFrame(
                self._stats_frame, fg_color=_SURFACE3,
                corner_radius=8, border_width=1, border_color=_LINE,
            )
            card.grid(row=0, column=col, sticky="nsew",
                      padx=(0 if col == 0 else 6, 0))
            card.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(card, text=title, font=_font(10),
                         text_color=_INK3).grid(row=0, column=0, pady=(8, 2))
            val_lbl = ctk.CTkLabel(card, text="—", font=_font(18, "bold"),
                                   text_color=_ACCENT)
            val_lbl.grid(row=1, column=0, pady=(0, 8))
            setattr(self, attr, val_lbl)

        # Data source timestamp label (row 1, hidden until first calculate)
        self._results_data_label = ctk.CTkLabel(
            self._results_frame,
            text="", font=_font(10), text_color=_INK4, anchor="w",
        )
        self._results_data_label.grid(row=1, column=0, sticky="w",
                                       padx=(16, 4), pady=(0, 2))
        self._results_data_label.grid_remove()

        # Matplotlib chart (hidden until first calculate, now at row 2)
        self._fig = Figure(facecolor=_BG, dpi=96)
        self._canvas = FigureCanvasTkAgg(self._fig, master=self._results_frame)
        self._canvas.get_tk_widget().grid(row=0, column=1, rowspan=3, sticky="nsew",
                                          padx=(4, 16), pady=16)
        self._canvas.get_tk_widget().grid_remove()

        self._build_risk_panel()

    # -------------------------------------------------------- Risk panel ------

    def _build_risk_panel(self):
        """Create the two-column risk factors panel (hidden until first Calculate)."""
        outer = ctk.CTkFrame(
            self._results_frame,
            fg_color=_SURFACE3,
            corner_radius=8,
            border_width=1,
            border_color=_LINE,
        )
        outer.grid(row=2, column=0, sticky="nsew", padx=(16, 4), pady=(0, 16))
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_remove()
        self._risk_panel = outer

        body = ctk.CTkFrame(outer, fg_color="transparent")
        body.grid(row=0, column=0, sticky="ew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=0)
        body.grid_columnconfigure(2, weight=1)

        # Vertical divider
        ctk.CTkFrame(body, fg_color=_LINE, width=1, corner_radius=0).grid(
            row=0, column=1, rowspan=1, sticky="ns",
        )

        self._risk_left  = ctk.CTkFrame(body, fg_color="transparent")
        self._risk_left.grid(row=0, column=0, sticky="nsew", pady=(6, 10))
        self._risk_left.grid_columnconfigure(0, weight=1)

        self._risk_right = ctk.CTkFrame(body, fg_color="transparent")
        self._risk_right.grid(row=0, column=2, sticky="nsew", pady=(6, 10))
        self._risk_right.grid_columnconfigure(0, weight=1)

    def _update_risk_panel(self, positive, negative):
        """Populate (or repopulate) the risk factors panel from evaluate_risk_factors output."""
        for w in self._risk_left.winfo_children():
            w.destroy()
        for w in self._risk_right.winfo_children():
            w.destroy()

        _ORDER = {"high": 0, "medium": 1, "low": 2}
        MAX = 8

        pos = sorted(positive, key=lambda x: _ORDER.get(x[1], 3))
        neg = sorted(negative, key=lambda x: _ORDER.get(x[1], 3))
        pos_extra = max(0, len(pos) - MAX)
        neg_extra = max(0, len(neg) - MAX)
        pos = pos[:MAX]
        neg = neg[:MAX]

        if not pos:
            ctk.CTkLabel(
                self._risk_left,
                text="Enter optional fields for full analysis",
                font=_font(10), text_color=_INK4, anchor="w", wraplength=240,
            ).grid(row=0, column=0, sticky="w", padx=14, pady=3)
        else:
            for i, (label, _impact) in enumerate(pos):
                ctk.CTkLabel(
                    self._risk_left, text=f"✅  {label}",
                    font=_font(10), text_color="#27ae60", anchor="w",
                ).grid(row=i, column=0, sticky="w", padx=14, pady=2)
            if pos_extra > 0:
                ctk.CTkLabel(
                    self._risk_left, text=f"+ {pos_extra} more",
                    font=_font(9), text_color=_INK4, anchor="w",
                ).grid(row=len(pos), column=0, sticky="w", padx=14, pady=(0, 4))

        if not neg:
            ctk.CTkLabel(
                self._risk_right,
                text="No significant risk factors identified",
                font=_font(10), text_color=_INK3, anchor="w", wraplength=240,
            ).grid(row=0, column=0, sticky="w", padx=14, pady=3)
        else:
            for i, (label, _impact) in enumerate(neg):
                color = _WARN if "not entered" in label.lower() else "#e74c3c"
                ctk.CTkLabel(
                    self._risk_right, text=f"❌  {label}",
                    font=_font(10), text_color=color, anchor="w",
                ).grid(row=i, column=0, sticky="w", padx=14, pady=2)
            if neg_extra > 0:
                ctk.CTkLabel(
                    self._risk_right, text=f"+ {neg_extra} more",
                    font=_font(9), text_color=_INK4, anchor="w",
                ).grid(row=len(neg), column=0, sticky="w", padx=14, pady=(0, 4))

        self._risk_panel.grid()

    # ----------------------------------------------------------------- P1 --

    _AH_FIELDS = [
        ("age",          "Age",          "yrs"),
        ("sex",          "Sex",          ""),
        ("weight_lb",    "Weight",       "lb"),
        ("height_in",    "Height",       "in"),
        ("bmi",          "BMI",          ""),
        ("systolic_bp",  "Systolic BP",  "mmHg"),
        ("resting_hr",   "Resting HR",   "bpm"),
        ("hrv",          "HRV",          "ms"),
        ("vo2_max",      "VO2 Max",      "mL/min·kg"),
        ("blood_glucose","Blood Glucose","mg/dL"),
    ]

    def _build_panel_ah(self, parent) -> ctk.CTkFrame:
        f = _panel(parent)
        f.grid_columnconfigure(1, weight=1)
        f.grid_columnconfigure(2, weight=0)

        hdr = _panel_header(f, "Apple Health")

        # Window selector in header (columns: 0=title, 1=window, 2=status)
        ctk.CTkComboBox(
            hdr,
            values=list(_WINDOW_OPTS.keys()),
            variable=self._window_var,
            state="readonly",
            width=112, height=26,
            font=_font(11),
            border_color=_LINE, fg_color=_BG,
            button_color=_LINE, button_hover_color="#d0d8e0",
            dropdown_fg_color=_BG, dropdown_text_color=_INK1,
            command=lambda _val: self._on_window_change(),
        ).grid(row=0, column=1, sticky="e", padx=(0, 8), pady=9)

        self._ah_status = ctk.CTkLabel(
            hdr, text="Loading...", font=_font(11), text_color=_INK4,
        )
        self._ah_status.grid(row=0, column=2, sticky="e", padx=14)

        # ── Date mode row (row 2): [● Current] [● Historical] [Month▾] [Year▾] ──
        date_row = ctk.CTkFrame(f, fg_color="transparent")
        date_row.grid(row=2, column=0, columnspan=3, sticky="ew",
                      padx=12, pady=(6, 2))
        for col in range(5):
            date_row.grid_columnconfigure(col, weight=0)
        date_row.grid_columnconfigure(4, weight=1)  # trailing spacer

        ctk.CTkRadioButton(
            date_row, text="Current", variable=self._ah_mode_var, value="current",
            command=self._on_ah_date_mode_change,
            font=_font(11), text_color=_INK2,
            fg_color=_ACCENT, hover_color=_ACCENT_HVR,
            radiobutton_width=14, radiobutton_height=14,
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkRadioButton(
            date_row, text="Historical", variable=self._ah_mode_var, value="historical",
            command=self._on_ah_date_mode_change,
            font=_font(11), text_color=_INK2,
            fg_color=_ACCENT, hover_color=_ACCENT_HVR,
            radiobutton_width=14, radiobutton_height=14,
        ).grid(row=0, column=1, sticky="w", padx=(12, 4))

        _now_dt = datetime.now()
        year_opts = [str(y) for y in range(_now_dt.year - 9, _now_dt.year + 1)]

        self._ah_month_cb = ctk.CTkComboBox(
            date_row,
            values=_MONTHS,
            variable=self._ah_month_var,
            state="readonly",
            width=72, height=24,
            font=_font(11),
            border_color=_LINE, fg_color=_BG,
            button_color=_LINE, button_hover_color="#d0d8e0",
            dropdown_fg_color=_BG, dropdown_text_color=_INK1,
            command=self._on_ah_historical_date_change,
        )
        self._ah_month_cb.grid(row=0, column=2, padx=(4, 2))
        self._ah_month_cb.grid_remove()

        self._ah_year_cb = ctk.CTkComboBox(
            date_row,
            values=year_opts,
            variable=self._ah_year_var,
            state="readonly",
            width=68, height=24,
            font=_font(11),
            border_color=_LINE, fg_color=_BG,
            button_color=_LINE, button_hover_color="#d0d8e0",
            dropdown_fg_color=_BG, dropdown_text_color=_INK1,
            command=self._on_ah_historical_date_change,
        )
        self._ah_year_cb.grid(row=0, column=3, padx=(0, 2))
        self._ah_year_cb.grid_remove()

        # ── Fields (row 3 onwards) ─────────────────────────────────────────────
        for i, (key, label, unit) in enumerate(self._AH_FIELDS):
            r = i + 3
            dot = ctk.CTkLabel(f, text="●", font=_font(10),
                               text_color=_INK4, width=18, anchor="w")
            dot.grid(row=r, column=0, sticky="w", padx=(12, 0), pady=3)
            self._ah_dots[key] = dot

            ctk.CTkLabel(f, text=label, font=_font(12), text_color=_INK2,
                         anchor="w").grid(
                row=r, column=1, sticky="w", padx=(4, 8), pady=3)

            # Read-only label (shown in AH modes)
            val_lbl = ctk.CTkLabel(f, text="—", font=_font(12),
                                   text_color=_INK4, anchor="e")
            val_lbl.grid(row=r, column=2, sticky="e", padx=12, pady=3)
            self._ah_labels[key] = val_lbl

            # Editable entry (shown in None mode, hidden initially)
            var = ctk.StringVar()
            self._ah_vars[key] = var
            ent = self._entry(f, var,
                              placeholder_text=unit or key,
                              width=120, height=26)
            ent.grid(row=r, column=2, sticky="e", padx=12, pady=3)
            ent.grid_remove()
            self._ah_entries[key] = ent

        # BMI auto-compute: fires whenever weight or height var changes (None mode)
        def _bmi_trace(*_args):
            try:
                w = float(self._ah_vars["weight_lb"].get())
                h = float(self._ah_vars["height_in"].get())
                if h <= 0:
                    raise ValueError
                bmi = round((w / h ** 2) * 703.0, 1)
                self._ah_vars["bmi"].set(str(bmi))
                self._ah_labels["bmi"].configure(text=str(bmi), text_color=_INK1)
                self._ah_dots["bmi"].configure(text_color=_OK)
            except ValueError:
                self._ah_vars["bmi"].set("")
                self._ah_labels["bmi"].configure(text="—", text_color=_INK4)
                self._ah_dots["bmi"].configure(text_color=_WARN)

        self._ah_vars["weight_lb"].trace_add("write", _bmi_trace)
        self._ah_vars["height_in"].trace_add("write", _bmi_trace)

        self._ah_timestamp = ctk.CTkLabel(
            f, text="", font=_font(10), text_color=_INK4, anchor="w",
        )
        self._ah_timestamp.grid(
            row=len(self._AH_FIELDS) + 3, column=0, columnspan=3,
            sticky="w", padx=12, pady=(4, 12),
        )
        return f

    # ----------------------------------------------------------------- P2 --

    _LAB_FIELDS = [
        ("total_cholesterol", "Total Cholesterol", "mg/dL"),
        ("hdl",               "HDL",               "mg/dL"),
        ("ldl",               "LDL",               "mg/dL"),
        ("triglycerides",     "Triglycerides",     "mg/dL"),
        ("apob",              "ApoB",              "mg/dL"),
        ("crp",               "CRP",               "mg/L"),
    ]

    def _build_panel_lab(self, parent) -> ctk.CTkFrame:
        f = _panel(parent)
        f.grid_columnconfigure(1, weight=1)

        _panel_header(f, "Lab Results")

        # ── Drop zone (plain tk widgets so DnD events fire) ────────────
        drop_outer = tk.Frame(f, bg=_SURFACE3, relief="solid", bd=1, height=60)
        drop_outer.grid(row=2, column=0, columnspan=2, sticky="ew",
                        padx=14, pady=(14, 4))
        drop_outer.grid_propagate(False)
        self._drop_label = tk.Label(
            drop_outer,
            text="Drop blood panel PDF here",
            bg=_SURFACE3, fg=_INK4, font=("Segoe UI", 11),
        )
        self._drop_label.pack(expand=True)
        if self._dnd_ok:
            self._drop_label.drop_target_register(DND_FILES)
            self._drop_label.dnd_bind("<<Drop>>", self._on_lab_drop)

        # ── Browse button (always visible reliable fallback) ───────────
        ctk.CTkButton(
            f, text="Browse for PDF...",
            height=28, corner_radius=6, font=_font(11),
            fg_color=_SURFACE3, hover_color=_LINE,
            text_color=_INK2, border_color=_BORDER, border_width=1,
            command=self._browse_lab_file,
        ).grid(row=3, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 2))

        # ── Status line ────────────────────────────────────────────────
        self._lab_status = ctk.CTkLabel(
            f, text="", font=_font(10), text_color=_INK3, anchor="w",
            wraplength=260,
        )
        self._lab_status.grid(row=4, column=0, columnspan=2,
                               sticky="w", padx=14, pady=(0, 6))

        # ── Lab value entry fields (rows 5+) ───────────────────────────
        self._lab_vars:    Dict[str, ctk.StringVar]  = {}
        self._lab_entries: Dict[str, ctk.CTkEntry]   = {}
        for i, (key, label, unit) in enumerate(self._LAB_FIELDS):
            r = i + 5
            ctk.CTkLabel(f, text=label, font=_font(12), text_color=_INK2,
                         anchor="w").grid(
                row=r, column=0, sticky="w", padx=14, pady=4)
            var = ctk.StringVar()
            self._lab_vars[key] = var
            ent = self._entry(f, var, placeholder_text=unit, width=96, height=30)
            ent.grid(row=r, column=1, sticky="e", padx=14, pady=4)
            self._lab_entries[key] = ent

        # ── CRP reference ranges + FocusOut color feedback ────────────
        n = len(self._LAB_FIELDS)
        ctk.CTkLabel(
            f,
            text="<1.0 low risk  ·  1–3 moderate  ·  >3 elevated  ·  >10 possible infection",
            font=_font(9), text_color=_INK4, anchor="w",
        ).grid(row=n + 5, column=0, columnspan=2, sticky="w", padx=14, pady=(0, 2))

        self._crp_infection_warn = ctk.CTkLabel(
            f, text="", font=_font(9), text_color="#e74c3c",
            anchor="w", wraplength=260,
        )
        self._crp_infection_warn.grid(row=n + 6, column=0, columnspan=2,
                                       sticky="w", padx=14, pady=(0, 6))
        self._crp_infection_warn.grid_remove()

        def _on_crp_focusout(_ev=None):
            val_str = self._lab_vars["crp"].get().strip()
            ent = self._lab_entries["crp"]
            self._crp_infection_warn.grid_remove()
            if not val_str:
                ent.configure(text_color=_INK2)
                return
            crp = self._parse_crp_value(val_str)
            if crp is None:
                ent.configure(text_color="#e74c3c")
                return
            if crp <= 1.0:
                ent.configure(text_color=_OK)
            elif crp <= 3.0:
                ent.configure(text_color=_INK1)
            elif crp <= 10.0:
                ent.configure(text_color=_WARN)
            else:
                ent.configure(text_color="#e74c3c")
                self._crp_infection_warn.configure(
                    text="⚠  May indicate acute infection — retest when healthy"
                )
                self._crp_infection_warn.grid()

        self._lab_entries["crp"].bind("<FocusOut>", _on_crp_focusout)

        return f

    # ----------------------------------------------------------------- P3 --

    def _build_panel_manual(self, parent) -> ctk.CTkFrame:
        f = _panel(parent)
        f.grid_columnconfigure(1, weight=1)

        _panel_header(f, "Manual Entry & Fitness")

        # ── ZIP Code (row 2–3) ─────────────────────────────────────────
        ctk.CTkLabel(f, text="ZIP Code", font=_font(12),
                     text_color=_INK2, anchor="w").grid(
            row=2, column=0, sticky="w", padx=14, pady=(14, 5))
        zip_ent = self._entry(f, self._var_zip, placeholder_text="12345",
                              width=90, height=30)
        zip_ent.grid(row=2, column=1, sticky="e", padx=14, pady=(14, 5))
        zip_ent.bind("<FocusOut>", self._on_zip_focusout)
        zip_ent.bind("<Return>",   self._on_zip_focusout)

        self._zip_status = ctk.CTkLabel(
            f, text="", font=_font(9), text_color=_INK4,
            anchor="w", wraplength=220,
        )
        self._zip_status.grid(row=3, column=0, columnspan=2,
                               sticky="w", padx=14, pady=(0, 4))

        _separator(f, row=4, padx=14, pady=6)

        # ── Smoking status (row 5) ─────────────────────────────────────
        ctk.CTkLabel(f, text="Smoking status", font=_font(12),
                     text_color=_INK2, anchor="w").grid(
            row=5, column=0, sticky="w", padx=14, pady=(8, 5))
        self._var_smoker = ctk.StringVar(value="never")
        ctk.CTkComboBox(
            f, values=["never", "former", "current"],
            variable=self._var_smoker, state="readonly",
            width=110, height=30, font=_font(12),
            border_color=_LINE, fg_color=_BG,
            button_color=_LINE, button_hover_color="#d0d8e0",
            dropdown_fg_color=_BG, dropdown_text_color=_INK1,
        ).grid(row=5, column=1, sticky="e", padx=14, pady=(8, 5))

        # ── Diabetes (row 6) ───────────────────────────────────────────
        ctk.CTkLabel(f, text="Diabetes", font=_font(12),
                     text_color=_INK2, anchor="w").grid(
            row=6, column=0, sticky="w", padx=14, pady=5)
        self._var_diabetes = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            f, text="", variable=self._var_diabetes,
            width=24, checkbox_width=20, checkbox_height=20,
            fg_color=_ACCENT, hover_color=_ACCENT_HVR, border_color=_LINE,
        ).grid(row=6, column=1, sticky="e", padx=14, pady=5)

        _separator(f, row=7, padx=14, pady=8)

        # ── Fitness inputs (rows 8+) ───────────────────────────────────
        self._fitness_vars: Dict[str, ctk.StringVar] = {}
        fitness_cfg = [
            ("grip_kg",      "Grip Strength",  "kg",  "Dynamometer reading"),
            ("hang_seconds", "Dead Hang Time", "sec", "Hang to failure"),
        ]
        f.grid_columnconfigure(2, weight=0)
        for i, (key, label, unit, hint) in enumerate(fitness_cfg):
            r = 8 + i * 3
            ctk.CTkLabel(f, text=label, font=_font(12),
                         text_color=_INK2, anchor="w").grid(
                row=r, column=0, sticky="w", padx=14, pady=(6, 0))
            ctk.CTkLabel(f, text=hint, font=_font(10),
                         text_color=_INK4, anchor="w").grid(
                row=r + 1, column=0, sticky="w", padx=14, pady=(0, 4))
            var = ctk.StringVar()
            self._fitness_vars[key] = var
            self._entry(f, var, placeholder_text="",
                        width=80, height=30).grid(
                row=r, column=1, rowspan=2, sticky="e", padx=(14, 4), pady=4)
            ctk.CTkLabel(f, text=unit, font=_font(11),
                         text_color=_INK3, anchor="w").grid(
                row=r, column=2, sticky="sw", padx=(0, 14), pady=(6, 0))

        ctk.CTkLabel(
            f, text="Improving these scores extends your curve.",
            font=_font(11), text_color=_INK4,
            wraplength=200, justify="left", anchor="w",
        ).grid(row=14, column=0, columnspan=2, sticky="w",
               padx=14, pady=(6, 14))

        return f

    # -------------------------------------------------------- Apple Health --

    def _on_window_change(self):
        """Called when the window selector changes (or on first load)."""
        if self._window_var.get() == "None":
            # Pre-fill entries with last-known AH values, then make editable.
            # BMI is always computed from weight/height; never editable directly.
            for key, var in self._ah_vars.items():
                if key == "bmi":
                    continue   # _bmi_trace updates BMI label as weight/height are set
                var.set(self._ah_raw.get(key, ""))
                self._ah_labels[key].grid_remove()
                self._ah_entries[key].grid()
            self._ah_status.configure(text="Manual entry", text_color=_INK3)
        else:
            # Switch back to read-only labels and reload from file
            for key in self._ah_vars:
                self._ah_entries[key].grid_remove()
                self._ah_labels[key].grid()
            if self._chart_shown:
                self._pending_recalculate = True
            self._load_apple_health_async()

    def _load_apple_health_async(self):
        window = _WINDOW_OPTS.get(self._window_var.get(), "last_value")
        as_of = self._get_as_of_datetime()

        def _worker():
            try:
                from src.apple_health import get_latest_biometrics
                data = get_latest_biometrics(_EXPORT_PATH, window=window, as_of=as_of)
                self.after(0, lambda: self._on_ah_loaded(data))
            except FileNotFoundError:
                self.after(0, self._on_ah_not_found)
            except Exception as exc:
                self.after(0, lambda: self._on_ah_error(str(exc)))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_ah_loaded(self, data: Dict):
        self._ah_data = data
        counts = data.get("_counts", {})
        found = sum(1 for k, _, _ in self._AH_FIELDS if data.get(k) is not None)
        total = len(self._AH_FIELDS)

        # Update year dropdown from the export's actual date range
        date_range = data.get("_date_range")
        if date_range and self._ah_year_cb:
            min_dt, max_dt = date_range
            if min_dt and max_dt:
                year_opts = [str(y) for y in range(min_dt.year, max_dt.year + 1)]
                if year_opts:
                    self._ah_year_cb.configure(values=year_opts)
                    if self._ah_year_var.get() not in year_opts:
                        self._ah_year_var.set(year_opts[-1])

        # Status text differs for historical vs current mode
        if self._ah_mode_var.get() == "historical":
            m = self._ah_month_var.get()
            y = self._ah_year_var.get()
            if found == 0:
                self._ah_status.configure(
                    text=f"No data found before {m} {y}", text_color=_WARN,
                )
            else:
                self._ah_status.configure(
                    text=f"as of {m} {y}  •  {found}/{total} fields",
                    text_color=_OK,
                )
        else:
            self._ah_status.configure(
                text=f"Connected  •  {found}/{total} fields",
                text_color=_OK,
            )

        self._ah_timestamp.configure(
            text=f"Loaded {datetime.now().strftime('%H:%M:%S')}",
        )

        for key, _label, unit in self._AH_FIELDS:
            val = data.get(key)
            dot = self._ah_dots[key]
            lbl = self._ah_labels[key]
            if val is not None:
                dot.configure(text_color=_OK)
                n = counts.get(key)
                suffix = f"  (avg {n})" if n and n > 1 else ""
                lbl.configure(
                    text=f"{val} {unit}{suffix}".strip(),
                    text_color=_INK1,
                    font=_font(12),
                )
                self._ah_raw[key] = str(val)
            else:
                dot.configure(text_color=_WARN)
                lbl.configure(text="—", text_color=_INK4)
                self._ah_raw.pop(key, None)

        # Auto-recalculate if a chart is already showing and mode changed
        if self._pending_recalculate:
            self._pending_recalculate = False
            self._on_calculate()

    def _on_ah_not_found(self):
        self._ah_status.configure(text="Not found", text_color=_WARN)
        self._ah_timestamp.configure(
            text="Place export.xml in Downloads and restart",
        )
        for dot in self._ah_dots.values():
            dot.configure(text_color=_WARN)

    def _on_ah_error(self, msg: str):
        self._ah_status.configure(text="Error", text_color="#e74c3c")
        self._ah_timestamp.configure(text=msg[:80])

    # -------------------------------------------------------- ZIP lookup ------

    def _on_zip_focusout(self, _ev=None):
        self._do_zip_lookup(self._var_zip.get().strip())

    def _do_zip_lookup(self, val: str):
        if not val:
            self._zip_info = {}
            self._zip_status.configure(text="", text_color=_INK4)
            return
        if not re.match(r"^\d{5}$", val):
            self._zip_info = {}
            self._zip_status.configure(text="Enter a 5-digit ZIP", text_color=_WARN)
            return
        self._zip_status.configure(text="Looking up…", text_color=_INK4)

        def _worker():
            from src.health_models import geo_zip_info, _ZIP_CACHE_PATH
            cache_exists = _ZIP_CACHE_PATH.exists()
            info = geo_zip_info(val)
            self.after(0, lambda: self._on_zip_result(val, info, cache_exists))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_zip_result(self, val: str, info: Dict, cache_exists: bool = True):
        self._zip_info = info
        if not info.get("found"):
            if not cache_exists:
                self._zip_status.configure(
                    text="Geographic data not yet downloaded — national baseline used",
                    text_color=_INK3,
                )
            else:
                self._zip_status.configure(
                    text="ZIP not in geographic dataset — national baseline used",
                    text_color=_INK3,
                )
            return
        county = info.get("county_name", "")
        state  = info.get("state_abbr", "")
        le     = info.get("life_expectancy", 0.0)
        offset = info.get("offset", 0.0)
        loc    = f"{county}, {state}" if county and state else (county or state or val)
        sign   = "+" if offset >= 0 else ""
        self._zip_status.configure(
            text=f"{loc}  •  LE {le:.1f} yrs  ({sign}{offset:.1f} vs national avg)",
            text_color=_OK if offset >= 0 else _WARN,
        )

    # ------------------------------------------------------- Date mode helpers

    def _get_as_of_datetime(self) -> Optional[datetime]:
        """Return last-second of the selected month/year, or None for current mode."""
        if self._ah_mode_var.get() != "historical":
            return None
        try:
            month_num = _MONTHS.index(self._ah_month_var.get()) + 1
            year_num = int(self._ah_year_var.get())
            last_day = calendar.monthrange(year_num, month_num)[1]
            return datetime(year_num, month_num, last_day, 23, 59, 59)
        except (ValueError, IndexError):
            return None

    def _on_ah_date_mode_change(self):
        """Called when Current/Historical radio button changes."""
        mode = self._ah_mode_var.get()
        if mode == "historical":
            if self._ah_month_cb:
                self._ah_month_cb.grid()
            if self._ah_year_cb:
                self._ah_year_cb.grid()
        else:
            if self._ah_month_cb:
                self._ah_month_cb.grid_remove()
            if self._ah_year_cb:
                self._ah_year_cb.grid_remove()
        if self._window_var.get() != "None":
            if self._chart_shown:
                self._pending_recalculate = True
            self._load_apple_health_async()

    def _on_ah_historical_date_change(self, _val=None):
        """Called when the month or year combobox changes in Historical mode."""
        if self._ah_mode_var.get() == "historical" and self._window_var.get() != "None":
            if self._chart_shown:
                self._pending_recalculate = True
            self._load_apple_health_async()

    # ----------------------------------------------------------- Lab parsing --

    def _on_lab_drop(self, event):
        # Strip whitespace and curly braces (Windows wraps spaced paths in {})
        path = event.data.strip().strip("{}")
        print(f"[DnD] Dropped: {path}")
        self._parse_lab_async(path)

    def _browse_lab_file(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="Select lab report",
            filetypes=[
                ("PDF files", "*.pdf"),
                ("Images", "*.jpg *.jpeg *.png *.webp"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self._parse_lab_async(path)

    def _parse_lab_async(self, file_path: str):
        fname = Path(file_path).name
        self._drop_label.config(text=f"Reading {fname}...")
        self._lab_status.configure(text="Reading PDF...", text_color=_INK3)

        def _worker():
            try:
                from src.lab_parser import parse_lab_pdf
                data = parse_lab_pdf(file_path)
                self.after(0, lambda: self._on_lab_parsed(file_path, data))
            except Exception as exc:
                self.after(0, lambda: self._on_lab_error(file_path, exc))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_lab_parsed(self, file_path: str, data: Dict):
        try:
            self._do_populate_lab(file_path, data)
        except Exception as exc:
            print(f"[Lab] Unexpected error in _on_lab_parsed: {exc}")
            self._drop_label.config(text=Path(file_path).name)
            self._lab_status.configure(
                text=f"Parse error — please enter values manually ({exc})",
                text_color=_WARN,
            )

    def _do_populate_lab(self, file_path: str, data: Dict):
        fname  = Path(file_path).name
        vendor = data.get("lab_vendor", "unknown").replace("_", " ").title()
        date   = data.get("lab_date", "")

        # Map parser keys → UI var keys.  ApoB is optional — absence must
        # never prevent the other fields from populating.
        field_map = {
            "total_cholesterol": "total_cholesterol",
            "hdl":               "hdl",
            "ldl":               "ldl",
            "triglycerides":     "triglycerides",
            "apob":              "apob",   # optional — may not be on every panel
            "crp":               "crp",   # optional — not always ordered
        }
        _silent_absent = {"crp"}   # not reported as "not on panel" when missing
        populated, missing = 0, []
        for parser_key, var_key in field_map.items():
            try:
                val = data.get(parser_key)
                if val is None:
                    if parser_key not in _silent_absent:
                        missing.append(parser_key)
                    continue
                self._lab_vars[var_key].set(str(round(float(val), 1)))
                populated += 1
            except Exception as field_exc:
                print(f"[Lab] Could not populate {parser_key}: {field_exc}")
                missing.append(parser_key)

        # Store extras (crp, hba1c, glucose) for model — never raise here
        self._lab_extras = {
            k: data.get(k) for k in ("crp", "hba1c", "glucose")
        }

        self._drop_label.config(text=fname)

        # Status line
        numeric_keys = [
            "total_cholesterol", "hdl", "ldl", "triglycerides", "apob",
            "hba1c", "glucose", "crp", "vldl", "non_hdl",
        ]
        n_extracted = sum(1 for k in numeric_keys if data.get(k) is not None)
        date_part = f" — {date}" if date else ""
        status = f"{vendor}{date_part} — {n_extracted} values extracted"
        _short = {
            "total_cholesterol": "TC", "hdl": "HDL", "ldl": "LDL",
            "triglycerides": "Trig", "apob": "ApoB",
        }
        if missing:
            status += f" ({', '.join(_short.get(m, m) for m in missing)} not on panel)"
        self._lab_status.configure(
            text=status,
            text_color=_OK if populated > 0 else _WARN,
        )
        print(f"[Lab] {status} | populated={populated}")

    def _on_lab_error(self, file_path: str, exc: Exception):
        print(f"[Lab] Error: {exc}")
        self._drop_label.config(text=Path(file_path).name)
        self._lab_status.configure(
            text="Could not read PDF — please enter values manually",
            text_color=_WARN,
        )

    # ---------------------------------------------------------- Calculate --

    @staticmethod
    def _parse_float(s: str) -> Optional[float]:
        try:
            return float((s or "").strip())
        except ValueError:
            return None

    @staticmethod
    def _parse_crp_value(s: str) -> Optional[float]:
        """Parse CRP field; handles '<1' notation. Returns None for blank/invalid/out-of-range."""
        s = (s or "").strip()
        if not s:
            return None
        if s.startswith("<"):
            try:
                return float(s[1:].strip())   # <1 → 1.0; model applies 0.5 correction
            except ValueError:
                return None
        try:
            v = float(s)
            return v if 0.0 <= v <= 100.0 else None
        except ValueError:
            return None

    def _on_calculate(self):
        from src.health_models import (
            ascvd_10yr_risk,
            evaluate_risk_factors,
            geo_mx_offset,
            integrate_survival,
            predict_combined_hazard,
            summarize_survival,
        )

        # Range-validate an optional float: out-of-range treated as blank
        def _parse_range(v: Optional[float], lo=None, hi=None) -> Optional[float]:
            if v is None:
                return None
            if lo is not None and v < lo:
                return None
            if hi is not None and v > hi:
                return None
            return v

        if self._window_var.get() == "None":
            # Read typed values from the manual-entry fields
            def _s(key: str) -> str:
                return self._ah_vars[key].get().strip()
            def _f(key: str) -> Optional[float]:
                return self._parse_float(self._ah_vars[key].get())
            age_s = _s("age")
            age   = int(float(age_s)) if age_s else None
            sex_s = _s("sex").lower()
            sex   = sex_s if sex_s in ("male", "female") else "male"
            sbp   = _parse_range(_f("systolic_bp"), lo=70,  hi=260)
            vo2   = _parse_range(_f("vo2_max"),      lo=10,  hi=90)
            wlb   = _f("weight_lb")
            wt_kg = wlb * 0.453592 if wlb is not None else None
        else:
            d     = self._ah_data
            age   = d.get("age")
            sex   = d.get("sex") or "male"
            sbp   = d.get("systolic_bp")
            vo2   = d.get("vo2_max")
            wt_kg = d.get("weight_kg")

        if age is None:
            self._results_placeholder.configure(
                text="Age not found.\nEnter age in Apple Health panel or use None mode.",
                text_color=_WARN,
            )
            return

        tc  = self._parse_float(self._lab_vars["total_cholesterol"].get())
        hdl = self._parse_float(self._lab_vars["hdl"].get())
        ldl = self._parse_float(self._lab_vars["ldl"].get())

        smoker   = self._var_smoker.get()
        diabetes = bool(self._var_diabetes.get())

        grip_kg      = self._parse_float(self._fitness_vars["grip_kg"].get())
        hang_seconds = self._parse_float(self._fitness_vars["hang_seconds"].get())

        # Warning for missing high-impact optional inputs
        missing_high = []
        if vo2 is None:
            missing_high.append("VO2 Max")
        if grip_kg is None and hang_seconds is None:
            missing_high.append("Grip Strength")
        if missing_high:
            self._missing_warn.configure(
                text=f"⚠  {' and '.join(missing_high)} not entered"
                     f" — fitness layers using neutral estimate"
            )
            self._missing_warn.grid()
        else:
            self._missing_warn.grid_remove()

        crp_raw   = self._parse_crp_value(self._lab_vars["crp"].get())
        crp_model = 0.5 if crp_raw is not None and crp_raw <= 1.0 else crp_raw

        features = {
            "age":               age,
            "sex":               sex,
            "race":              "white",
            "total_cholesterol": tc,
            "hdl":               hdl,
            "systolic_bp":       sbp,
            "smoker":            smoker,
            "diabetes":          diabetes,
            "bp_treated":        False,
            "vo2_max":           vo2,
            "grip_kg":           grip_kg,
            "hang_seconds":      hang_seconds,
            "weight_kg":         wt_kg,
            "crp":               crp_model,
        }

        # Extra fields used by risk factor evaluation (not the hazard model)
        features["ldl"]           = ldl
        features["triglycerides"] = self._parse_float(self._lab_vars["triglycerides"].get())
        features["apob"]          = self._parse_float(self._lab_vars["apob"].get())
        features["hba1c"]         = self._lab_extras.get("hba1c")
        features["crp_raw"]       = crp_raw
        if self._window_var.get() == "None":
            features["bmi"]           = self._parse_float(self._ah_vars["bmi"].get())
            features["resting_hr"]    = self._parse_float(self._ah_vars["resting_hr"].get())
            features["hrv"]           = self._parse_float(self._ah_vars["hrv"].get())
            features["blood_glucose"] = self._parse_float(self._ah_vars["blood_glucose"].get())
        else:
            _ad = self._ah_data
            features["bmi"]           = _ad.get("bmi")
            features["resting_hr"]    = _ad.get("resting_hr")
            features["hrv"]           = _ad.get("hrv")
            features["blood_glucose"] = _ad.get("blood_glucose")

        zip_code = self._var_zip.get().strip()
        geo      = geo_mx_offset(zip_code)

        rh       = predict_combined_hazard(features)
        rh_geo   = rh * geo
        curve    = integrate_survival(age, sex, rel_hazard=rh_geo)
        r5, r10, med = summarize_survival(curve, age)

        ascvd = None
        if tc and hdl:
            ascvd = ascvd_10yr_risk(features)

        # Baseline shifts to local geography when ZIP is known
        baseline_curve = integrate_survival(age, sex, rel_hazard=geo)

        print("\n--- Longevity Calculator Results ---")
        print(f"Age {age}  sex={sex}  smoker={smoker}  diabetes={diabetes}")
        if tc:  print(f"TC={tc}  HDL={hdl}  LDL={ldl}")
        if vo2: print(f"VO2 max={vo2}  grip={grip_kg}kg  hang={hang_seconds}s")
        print(f"Combined rel_hazard : {rh:.3f}  geo={geo:.3f}  combined={rh_geo:.3f}")
        if ascvd is not None:
            print(f"10yr ASCVD risk     : {ascvd * 100:.1f}%")
        print(f"5yr / 10yr risk     : {r5*100:.1f}% / {r10*100:.1f}%")
        print(f"Median remaining    : {med:.1f} yrs")

        # Determine data label and source description for chart + timestamp
        window_val = self._window_var.get()
        if window_val == "None":
            data_label = "You (manual)"
            source_text = "Based on manually entered data"
        elif self._ah_mode_var.get() == "historical":
            m = self._ah_month_var.get()
            y = self._ah_year_var.get()
            data_label = f"You ({m} {y})"
            source_text = f"Based on Apple Health data as of {m} {y}"
        else:
            data_label = "You"
            source_text = "Based on current Apple Health data"

        # Baseline label and ZIP annotation
        zip_info = self._zip_info
        if zip_info.get("found"):
            county  = zip_info.get("county_name", "")
            state   = zip_info.get("state_abbr",  "")
            loc     = f"{county}, {state}" if county and state else zip_code
            offset  = zip_info.get("offset", 0.0)
            sign    = "+" if offset >= 0 else ""
            baseline_label = f"{loc} average"
            source_text += f"  •  ZIP {zip_code} ({loc}, LE {sign}{offset:.1f} vs national)"
        else:
            baseline_label = "Population average"

        # Reveal chart area, hide placeholder
        self._results_placeholder.grid_remove()
        self._stats_frame.grid()
        self._results_data_label.configure(text=source_text)
        self._results_data_label.grid()
        self._canvas.get_tk_widget().grid()
        self._chart_shown = True

        # Update stat cards
        self._stat_r5.configure(text=f"{r5*100:.1f}%")
        self._stat_r10.configure(text=f"{r10*100:.1f}%")
        self._stat_med.configure(text=f"{med:.0f} yrs")
        self._stat_ascvd.configure(
            text=f"{ascvd*100:.1f}%" if ascvd is not None else "—"
        )

        self._update_chart(age, curve, baseline_curve, r5, r10, med, rh_geo,
                           data_label=data_label, baseline_label=baseline_label)

        factors = evaluate_risk_factors(features)
        self._update_risk_panel(factors["positive"], factors["negative"])


    def _update_chart(
        self,
        age0: int,
        actual_curve: List[Dict],
        baseline_curve: List[Dict],
        r5: float,
        r10: float,
        med: float,
        rh: float,
        data_label: str = "You",
        baseline_label: str = "Population average",
    ):
        self._fig.clear()
        ax = self._fig.add_subplot(111)
        ax.set_facecolor(_BG)

        # x = years from now, y = survival %
        yb = [r["age"] - age0 for r in baseline_curve]
        sb = [r["S"] * 100       for r in baseline_curve]
        ya = [r["age"] - age0 for r in actual_curve]
        sa = [r["S"] * 100       for r in actual_curve]

        # Shade area between curves
        x_fill = np.linspace(0, min(yb[-1], ya[-1]), 500)
        sb_i = np.interp(x_fill, yb, sb)
        sa_i = np.interp(x_fill, ya, sa)
        fill_color = _WARN if rh > 1.0 else _OK
        ax.fill_between(x_fill, sa_i, sb_i, alpha=0.10, color=fill_color)

        # Curves
        ax.plot(yb, sb, color=_INK4, linestyle="--", linewidth=1.5,
                label=baseline_label, alpha=0.75)
        ax.plot(ya, sa, color=_ACCENT, linewidth=2.5,
                label=f"{data_label}  (hazard {rh:.2f}×)")

        # 5-yr and 10-yr callouts
        for yr, risk, col in [(5, r5, _WARN), (10, r10, _ACCENT)]:
            if yr <= ya[-1]:
                s_val = (1 - risk) * 100
                ax.axvline(yr, color=col, linestyle=":", linewidth=1.1, alpha=0.6)
                ax.annotate(
                    f"{yr}-yr: {risk*100:.1f}% risk",
                    xy=(yr, s_val),
                    xytext=(yr + 1.5, min(s_val + 6, 98)),
                    fontsize=8.5, color=col,
                    arrowprops=dict(arrowstyle="-", color=col, lw=0.8),
                )

        # Median remaining years
        if 0 < med <= ya[-1]:
            ax.axvline(med, color=_OK, linestyle="--", linewidth=1.5, alpha=0.7)
            ax.text(med + 0.6, 53, f"Median\n{med:.0f} yrs",
                    fontsize=8.5, color=_OK, va="bottom")

        # Axes styling
        x_max = min(int(ya[-1]), 60)
        ax.set_xlim(0, x_max)
        ax.set_ylim(0, 105)
        ax.set_xlabel("Years from now", fontsize=10, color=_INK2)
        ax.set_ylabel("Survival probability (%)", fontsize=10, color=_INK2)
        ax.tick_params(colors=_INK3, labelsize=9)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        for spine in ("left", "bottom"):
            ax.spines[spine].set_color(_LINE)
        ax.yaxis.grid(True, color=_LINE, linewidth=0.7, alpha=0.8)
        ax.set_axisbelow(True)
        ax.legend(fontsize=9, framealpha=0.9, loc="upper right",
                  edgecolor=_LINE)

        self._fig.tight_layout(pad=1.5)
        self._canvas.draw()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    app = LongevityApp()
    app.mainloop()


if __name__ == "__main__":
    main()
