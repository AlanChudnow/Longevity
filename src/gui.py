"""
gui.py — Main application window (CustomTkinter).
Matches design/Longevity_Risk_Calculator_v2.html.

Layout:
  TOP:    Three input panels side-by-side
  MIDDLE: Calculate button (full width)
  BOTTOM: Results section (placeholder in Phase 4; chart embedded in Phase 5)
"""
from __future__ import annotations

import threading
import tkinter as tk
from datetime import datetime
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

class LongevityApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Fix 1 — initialize tkdnd on the root window (must happen before build)
        self._dnd_ok = False
        if _TKDND_AVAILABLE:
            try:
                TkinterDnD._require(self)
                self._dnd_ok = True
                print("[DnD] tkinterdnd2 initialised successfully.")
            except Exception as exc:
                print(f"[DnD] Init failed, drag-and-drop disabled: {exc}")

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
        self._drop_label = None   # tk.Label or None (DnD zone inner label)

        # Fix 3 — window selector state (default "Last value", not "None")
        self._window_var = tk.StringVar(value="Last value")

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

        ctk.CTkButton(
            self, text="CALCULATE",
            height=48, corner_radius=8,
            font=_font(14, "bold"),
            fg_color=_ACCENT, hover_color=_ACCENT_HVR, text_color="#ffffff",
            command=self._on_calculate,
        ).grid(row=1, column=0, sticky="ew", padx=20, pady=12)

        self._results_frame = _panel(self)
        self._results_frame.grid(row=2, column=0, sticky="nsew",
                                  padx=20, pady=(0, 20))
        self._results_frame.grid_columnconfigure(0, weight=1)
        self._results_frame.grid_rowconfigure(1, weight=1)

        # Placeholder — shown before first CALCULATE
        self._results_placeholder = ctk.CTkLabel(
            self._results_frame,
            text="Click CALCULATE to generate your survival curve.",
            font=_font(13), text_color=_INK4,
        )
        self._results_placeholder.grid(row=0, column=0, rowspan=2,
                                        padx=20, pady=60)

        # Stats strip (hidden until first calculate)
        self._stats_frame = ctk.CTkFrame(
            self._results_frame, fg_color="transparent",
        )
        self._stats_frame.grid(row=0, column=0, sticky="ew",
                                padx=16, pady=(12, 4))
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

        # Matplotlib chart (hidden until first calculate)
        self._fig = Figure(facecolor=_BG, dpi=96)
        self._canvas = FigureCanvasTkAgg(self._fig, master=self._results_frame)
        self._canvas.get_tk_widget().grid(row=1, column=0, sticky="nsew",
                                          padx=16, pady=(0, 16))
        self._canvas.get_tk_widget().grid_remove()

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

        # Fix 3 — window selector in header (columns: 0=title, 1=window, 2=status)
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

        for i, (key, label, unit) in enumerate(self._AH_FIELDS):
            r = i + 2
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
            ent.grid_remove()   # hidden until "None" mode
            self._ah_entries[key] = ent

        self._ah_timestamp = ctk.CTkLabel(
            f, text="", font=_font(10), text_color=_INK4, anchor="w",
        )
        self._ah_timestamp.grid(
            row=len(self._AH_FIELDS) + 2, column=0, columnspan=3,
            sticky="w", padx=12, pady=(4, 12),
        )
        return f

    # ----------------------------------------------------------------- P2 --

    _LAB_FIELDS = [
        ("total_cholesterol", "Total Cholesterol", "mg/dL"),
        ("hdl",               "HDL",               "mg/dL"),
        ("ldl",               "LDL",               "mg/dL"),
        ("apob",              "ApoB",              "mg/dL"),
    ]

    def _build_panel_lab(self, parent) -> ctk.CTkFrame:
        f = _panel(parent)
        f.grid_columnconfigure(1, weight=1)

        _panel_header(f, "Lab Results")

        # Fix 1 — DnD drop zone using plain tk widgets (CTk widgets don't get
        # drop_target_register after TkinterDnD._require patches tk.BaseWidget)
        if self._dnd_ok:
            drop_outer = tk.Frame(f, bg=_SURFACE3, relief="solid", bd=1, height=72)
            drop_outer.grid(row=2, column=0, columnspan=2, sticky="ew",
                            padx=14, pady=(14, 10))
            drop_outer.grid_propagate(False)

            self._drop_label = tk.Label(
                drop_outer,
                text="Drop blood panel PDF here",
                bg=_SURFACE3, fg=_INK4,
                font=("Segoe UI", 12),
            )
            self._drop_label.pack(expand=True)
            self._drop_label.drop_target_register(DND_FILES)
            self._drop_label.dnd_bind("<<Drop>>", self._on_lab_drop)
        else:
            # Fallback: static label + Browse button
            drop = ctk.CTkFrame(
                f, fg_color=_SURFACE3, corner_radius=8,
                border_width=1, border_color=_LINE, height=72,
            )
            drop.grid(row=2, column=0, columnspan=2, sticky="ew",
                      padx=14, pady=(14, 10))
            drop.grid_columnconfigure(0, weight=1)
            drop.grid_rowconfigure(0, weight=1)
            ctk.CTkLabel(
                drop, text="Drop PDF (DnD unavailable)",
                font=_font(12), text_color=_INK4,
            ).grid(row=0, column=0, padx=12, pady=12)

        self._lab_vars: Dict[str, ctk.StringVar] = {}
        for i, (key, label, unit) in enumerate(self._LAB_FIELDS):
            r = i + 3
            ctk.CTkLabel(f, text=label, font=_font(12), text_color=_INK2,
                         anchor="w").grid(
                row=r, column=0, sticky="w", padx=14, pady=5)
            var = ctk.StringVar()
            self._lab_vars[key] = var
            # Fix 2 — styled entry
            self._entry(f, var, placeholder_text=unit,
                        width=96, height=30).grid(
                row=r, column=1, sticky="e", padx=14, pady=5)

        return f

    # ----------------------------------------------------------------- P3 --

    def _build_panel_manual(self, parent) -> ctk.CTkFrame:
        f = _panel(parent)
        f.grid_columnconfigure(1, weight=1)

        _panel_header(f, "Manual Entry & Fitness")

        ctk.CTkLabel(f, text="Smoking status", font=_font(12),
                     text_color=_INK2, anchor="w").grid(
            row=2, column=0, sticky="w", padx=14, pady=(14, 5))
        self._var_smoker = ctk.StringVar(value="never")
        ctk.CTkComboBox(
            f, values=["never", "former", "current"],
            variable=self._var_smoker, state="readonly",
            width=110, height=30, font=_font(12),
            border_color=_LINE, fg_color=_BG,
            button_color=_LINE, button_hover_color="#d0d8e0",
            dropdown_fg_color=_BG, dropdown_text_color=_INK1,
        ).grid(row=2, column=1, sticky="e", padx=14, pady=(14, 5))

        ctk.CTkLabel(f, text="Diabetes", font=_font(12),
                     text_color=_INK2, anchor="w").grid(
            row=3, column=0, sticky="w", padx=14, pady=5)
        self._var_diabetes = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            f, text="", variable=self._var_diabetes,
            width=24, checkbox_width=20, checkbox_height=20,
            fg_color=_ACCENT, hover_color=_ACCENT_HVR, border_color=_LINE,
        ).grid(row=3, column=1, sticky="e", padx=14, pady=5)

        _separator(f, row=4, padx=14, pady=8)

        self._fitness_vars: Dict[str, ctk.StringVar] = {}
        fitness_cfg = [
            ("grip_kg",      "Grip Strength",  "kg",  "Dynamometer reading"),
            ("hang_seconds", "Dead Hang Time", "sec", "Hang to failure"),
        ]
        f.grid_columnconfigure(2, weight=0)
        for i, (key, label, unit, hint) in enumerate(fitness_cfg):
            r = 5 + i * 3
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
        ).grid(row=11, column=0, columnspan=2, sticky="w",
               padx=14, pady=(6, 14))

        return f

    # -------------------------------------------------------- Apple Health --

    def _on_window_change(self):
        """Called when the window selector changes (or on first load)."""
        if self._window_var.get() == "None":
            # Pre-fill entries with last-known AH values, then make editable
            for key, var in self._ah_vars.items():
                var.set(self._ah_raw.get(key, ""))
                self._ah_labels[key].grid_remove()
                self._ah_entries[key].grid()
            self._ah_status.configure(text="Manual entry", text_color=_INK3)
        else:
            # Switch back to read-only labels and reload from file
            for key in self._ah_vars:
                self._ah_entries[key].grid_remove()
                self._ah_labels[key].grid()
            self._load_apple_health_async()

    def _load_apple_health_async(self):
        window = _WINDOW_OPTS.get(self._window_var.get(), "last_value")

        def _worker():
            try:
                from src.apple_health import get_latest_biometrics
                data = get_latest_biometrics(_EXPORT_PATH, window=window)
                self.after(0, lambda: self._on_ah_loaded(data))
            except FileNotFoundError:
                self.after(0, self._on_ah_not_found)
            except Exception as exc:
                self.after(0, lambda: self._on_ah_error(str(exc)))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_ah_loaded(self, data: Dict):
        self._ah_data = data
        # Fix 3 — read averaging counts for "(avg N)" annotations
        counts = data.get("_counts", {})
        found = sum(1 for k, _, _ in self._AH_FIELDS if data.get(k) is not None)
        total = len(self._AH_FIELDS)

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
                self._ah_raw[key] = str(val)   # store numeric-only for None mode pre-fill
            else:
                dot.configure(text_color=_WARN)
                lbl.configure(text="—", text_color=_INK4)
                self._ah_raw.pop(key, None)

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

    # ----------------------------------------------------------- Lab parsing --

    def _on_lab_drop(self, event):
        path = event.data.strip()
        # tkinterdnd2 wraps paths with spaces in braces on Windows
        if path.startswith("{") and path.endswith("}"):
            path = path[1:-1]
        print(f"[DnD] Dropped: {path}")
        fname = Path(path).name
        if self._drop_label is not None:
            self._drop_label.config(text=f"Parsing {fname}...")
        self._parse_lab_async(path)

    def _parse_lab_async(self, file_path: str):
        def _worker():
            try:
                from src.lab_parser import extract_lab_values
                data = extract_lab_values(file_path)
                self.after(0, lambda: self._on_lab_parsed(file_path, data))
            except Exception as exc:
                self.after(0, lambda: self._on_lab_error(file_path, exc))
        threading.Thread(target=_worker, daemon=True).start()

    def _on_lab_parsed(self, file_path: str, data: Dict):
        fname = Path(file_path).name
        if self._drop_label is not None:
            self._drop_label.config(text=f"Parsed: {fname}")
        field_map = {
            "total_cholesterol": "total_cholesterol",
            "hdl":               "hdl",
            "ldl":               "ldl",
            "apob":              "apob",
        }
        populated = 0
        for api_key, var_key in field_map.items():
            val = data.get(api_key)
            if val is not None:
                self._lab_vars[var_key].set(str(round(val, 1)))
                populated += 1
        print(f"[Lab] Populated {populated}/4 fields: {data}")

    def _on_lab_error(self, file_path: str, exc: Exception):
        fname = Path(file_path).name
        msg = str(exc)
        print(f"[Lab] Error parsing {fname}: {msg}")
        if self._drop_label is not None:
            short = msg[:55] + "…" if len(msg) > 55 else msg
            self._drop_label.config(text=f"Error: {short}")

    # ---------------------------------------------------------- Calculate --

    @staticmethod
    def _parse_float(s: str) -> Optional[float]:
        try:
            return float((s or "").strip())
        except ValueError:
            return None

    def _on_calculate(self):
        from src.health_models import (
            ascvd_10yr_risk,
            integrate_survival,
            predict_combined_hazard,
            summarize_survival,
        )

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
            sbp   = _f("systolic_bp")
            vo2   = _f("vo2_max")
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
        }

        rh    = predict_combined_hazard(features)
        curve = integrate_survival(age, sex, rel_hazard=rh)
        r5, r10, med = summarize_survival(curve, age)

        ascvd = None
        if tc and hdl:
            ascvd = ascvd_10yr_risk(features)

        baseline_curve = integrate_survival(age, sex, rel_hazard=1.0)

        print("\n--- Longevity Calculator Results ---")
        print(f"Age {age}  sex={sex}  smoker={smoker}  diabetes={diabetes}")
        if tc:  print(f"TC={tc}  HDL={hdl}  LDL={ldl}")
        if vo2: print(f"VO2 max={vo2}  grip={grip_kg}kg  hang={hang_seconds}s")
        print(f"Combined rel_hazard : {rh:.3f}")
        if ascvd is not None:
            print(f"10yr ASCVD risk     : {ascvd * 100:.1f}%")
        print(f"5yr / 10yr risk     : {r5*100:.1f}% / {r10*100:.1f}%")
        print(f"Median remaining    : {med:.1f} yrs")

        # Reveal chart area, hide placeholder
        self._results_placeholder.grid_remove()
        self._stats_frame.grid()
        self._canvas.get_tk_widget().grid()

        # Update stat cards
        self._stat_r5.configure(text=f"{r5*100:.1f}%")
        self._stat_r10.configure(text=f"{r10*100:.1f}%")
        self._stat_med.configure(text=f"{med:.0f} yrs")
        self._stat_ascvd.configure(
            text=f"{ascvd*100:.1f}%" if ascvd is not None else "—"
        )

        self._update_chart(age, curve, baseline_curve, r5, r10, med, rh)


    def _update_chart(
        self,
        age0: int,
        actual_curve: List[Dict],
        baseline_curve: List[Dict],
        r5: float,
        r10: float,
        med: float,
        rh: float,
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
                label="Population baseline", alpha=0.75)
        ax.plot(ya, sa, color=_ACCENT, linewidth=2.5,
                label=f"Your trajectory  (hazard {rh:.2f}×)")

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
