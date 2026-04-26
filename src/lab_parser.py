"""
lab_parser.py

Parse blood panel PDFs from known lab vendors into a structured dict.
No API required — pure PDF-to-text + keyword matching.

Supported vendors (auto-detected):
  - Labcorp
  - Sonora Quest Laboratories

Adding a new vendor:
  1. Add a detection string to _detect_lab()
  2. Add a LABELS dict entry in _labels_for_lab()
  3. Add any vendor-specific preprocessing to _preprocess()

Returns dict with keys (all values float | None):
  total_cholesterol, hdl, ldl, non_hdl, triglycerides, apob,
  hba1c, glucose, crp, vldl
"""

from __future__ import annotations
import re
from pathlib import Path
from typing import Optional

try:
    import pdfplumber
except ImportError:
    raise ImportError("pdfplumber is required: pip install pdfplumber")


# ─────────────────────────────────────────────
# Vendor detection
# ─────────────────────────────────────────────

def _detect_lab(text: str) -> str:
    t = text.lower()
    if "labcorp" in t or "laboratory corporation" in t:
        return "labcorp"
    if "sonora quest" in t:
        return "sonora_quest"
    return "unknown"


# ─────────────────────────────────────────────
# Label maps per vendor
# Each key maps to a list of possible label strings,
# tried in order (first match wins).
# ─────────────────────────────────────────────

def _labels_for_lab(lab: str) -> dict[str, list[str]]:
    """
    Return ordered label-variant lists for each target value.
    More specific labels must come before generic ones to avoid
    substring collisions (e.g. "HDL Cholesterol" before "Cholesterol").
    """
    if lab == "labcorp":
        return {
            "hdl":               ["HDL Cholesterol 01", "HDL Cholesterol"],
            "ldl":               ["LDL Chol Calc (NIH)", "LDL Cholesterol"],
            "non_hdl":           ["Non-HDL Cholesterol"],
            "vldl":              ["VLDL Cholesterol Cal", "VLDL Cholesterol"],
            "total_cholesterol": ["Cholesterol, Total 01", "Cholesterol, Total"],
            "triglycerides":     ["Triglycerides 01", "Triglycerides"],
            "apob":              ["Apolipoprotein B 01", "Apolipoprotein B01", "Apolipoprotein B"],
            "hba1c":             ["Hemoglobin A1c 01", "Hemoglobin A1c"],
            "glucose":           ["Glucose 01", "Glucose"],
            "crp":               ["C-Reactive Protein, Quant 01", "C-Reactive Protein"],
        }

    if lab == "sonora_quest":
        return {
            "hdl":               ["HDL Cholesterol"],
            "ldl":               ["LDL Cholesterol, Calculated", "LDL Cholesterol"],
            "non_hdl":           ["Non-HDL Cholesterol"],
            "vldl":              ["VLDL Cholesterol"],
            "total_cholesterol": ["Cholesterol"],        # matched last, after HDL/Non-HDL
            "triglycerides":     ["Triglycerides"],
            "apob":              ["Apolipoprotein B"],
            "hba1c":             ["Hemoglobin A1c"],
            "glucose":           ["Glucose"],
            "crp":               ["C-Reactive Protein"],
        }

    # Unknown vendor — try common labels as a best-effort fallback
    return {
        "hdl":               ["HDL Cholesterol", "HDL-C", "HDL Chol"],
        "ldl":               ["LDL Cholesterol", "LDL-C", "LDL Chol"],
        "non_hdl":           ["Non-HDL Cholesterol", "Non HDL"],
        "vldl":              ["VLDL Cholesterol", "VLDL"],
        "total_cholesterol": ["Total Cholesterol", "Cholesterol, Total", "Cholesterol"],
        "triglycerides":     ["Triglycerides", "Trig"],
        "apob":              ["Apolipoprotein B", "Apo B", "ApoB"],
        "hba1c":             ["Hemoglobin A1c", "HbA1c", "A1c"],
        "glucose":           ["Glucose"],
        "crp":               ["C-Reactive Protein", "CRP"],
    }


# ─────────────────────────────────────────────
# Lines to always skip (ratio rows, section headers, etc.)
# ─────────────────────────────────────────────

_SKIP_PATTERNS = [
    r"cholesterol/hdl",       # ratio line, not a raw value
    r"bun/creatinine",        # ratio
    r"albumin/globulin",      # ratio
    r"values outside",        # duplicate section header
    r"reference range",       # table header
    r"risk categor",          # risk table
    r"optimal",               # risk table row
    r"borderline",            # risk table row
    r"near optimal",          # risk table row
]

def _should_skip(line: str) -> bool:
    low = line.lower()
    return any(re.search(p, low) for p in _SKIP_PATTERNS)


# ─────────────────────────────────────────────
# Number extraction
# ─────────────────────────────────────────────

def _extract_first_number(segment: str) -> Optional[float]:
    """
    Extract the first numeric value from the fragment after a label.

    Handles:
      "46 mg/dL >39"          → 46.0   (Labcorp HDL)
      "35 L ≥40 mg/dL"        → 35.0   (Sonora Quest HDL, flag L)
      "122 H * ≤99 mg/dL"     → 122.0  (Sonora Quest LDL, flag H, asterisk)
      "5.1 % 4.8-5.6"         → 5.1    (Labcorp HbA1c)
      "5.2* ≤5.6 %"           → 5.2    (Sonora Quest HbA1c, asterisk glued)
      "96* 70 - 99 mg/dL"     → 96.0   (Sonora Quest Glucose, asterisk glued)
      "<1 mg/L 0-10"          → 1.0    (CRP < prefix)
      "66 mg/dL <90"          → 66.0   (ApoB)
    """
    # Stop at the reference range boundary — indicated by a ≤ ≥ or
    # a standalone < / > that is followed by a digit (ref range prefix)
    # but NOT at <1 where < is the result itself.
    # Strategy: split on ≤ ≥ first (always ref-range), then handle < >
    seg = re.split(r'[≤≥]', segment)[0]

    # Remove glued asterisks and flag letters that appear immediately after a digit
    # e.g. "96*" → "96", "5.2*" → "5.2", but keep standalone text for stripping below
    seg = re.sub(r'(\d)\*', r'\1', seg)          # strip glued asterisk
    seg = re.sub(r'(\d)\s+[HLChlc]\b', r'\1', seg)  # strip trailing flag letter

    # Now extract the first integer or decimal
    match = re.search(r'\d+\.?\d*', seg)
    if match:
        return float(match.group())
    return None


# ─────────────────────────────────────────────
# Vendor-specific preprocessing
# ─────────────────────────────────────────────

def _preprocess(text: str, lab: str) -> str:
    """
    Trim sections from the full report text that would produce
    false positive matches (e.g. duplicated flagged-values section).
    """
    if lab == "sonora_quest":
        # The "Values Outside of Reference Range" table repeats values
        # already seen — cut it off to avoid double-matching
        cutoff = text.lower().find("values outside of reference range")
        if cutoff > 0:
            text = text[:cutoff]

    return text


# ─────────────────────────────────────────────
# Core line matching
# ─────────────────────────────────────────────

def _match_label(line: str, label: str) -> Optional[str]:
    """
    If `label` appears in `line` as a whole-word-ish match,
    return the portion of the line AFTER the label.
    Returns None if not matched.

    Uses a word-boundary-aware search so "Cholesterol" doesn't
    match inside "HDL Cholesterol" when we don't want it to.
    We handle ordering in the labels dict instead, but this
    also catches partial-word false positives like "Cholesterol Cal".
    """
    # Escape label for regex, then require it to start at a word boundary
    pattern = r'(?<![A-Za-z,])' + re.escape(label)
    m = re.search(pattern, line, re.IGNORECASE)
    if m:
        return line[m.end():]
    return None


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

def parse_lab_pdf(pdf_path: str | Path) -> dict:
    """
    Extract key blood panel values from a PDF lab report.

    Parameters
    ----------
    pdf_path : str or Path
        Path to the lab report PDF.

    Returns
    -------
    dict with keys:
        total_cholesterol, hdl, ldl, non_hdl, vldl,
        triglycerides, apob, hba1c, glucose, crp
        lab_vendor  (str: "labcorp" | "sonora_quest" | "unknown")
        lab_date    (str: ISO date if found, else "")

    All numeric values are float | None.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    # ── Extract full text ──────────────────────────────────────
    pages_text = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                pages_text.append(t)
    full_text = "\n".join(pages_text)

    # ── Detect vendor ──────────────────────────────────────────
    lab = _detect_lab(full_text)

    # ── Preprocess ─────────────────────────────────────────────
    working_text = _preprocess(full_text, lab)
    lines = working_text.splitlines()

    # ── Match labels → values ──────────────────────────────────
    labels = _labels_for_lab(lab)
    results: dict = {k: None for k in labels}

    for key, label_variants in labels.items():
        for label in label_variants:
            for line in lines:
                if _should_skip(line):
                    continue
                after = _match_label(line, label)
                if after is not None:
                    value = _extract_first_number(after)
                    if value is not None:
                        results[key] = value
                        break   # found value for this label variant
            if results[key] is not None:
                break           # found value for this key; skip remaining variants

    # ── Metadata ───────────────────────────────────────────────
    results["lab_vendor"] = lab

    date_match = re.search(
        r'(?:date collected|collected)[:\s]+(\d{1,2}/\d{1,2}/\d{4})',
        full_text, re.IGNORECASE
    )
    results["lab_date"] = date_match.group(1) if date_match else ""

    return results


# ─────────────────────────────────────────────
# CLI / test harness
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys, json

    paths = sys.argv[1:] if len(sys.argv) > 1 else []

    if not paths:
        print("Usage: python lab_parser.py path/to/report.pdf [another.pdf ...]")
        sys.exit(0)

    for path in paths:
        print(f"\n{'='*60}")
        print(f"FILE: {Path(path).name}")
        print('='*60)
        try:
            result = parse_lab_pdf(path)
            vendor = result.pop("lab_vendor")
            date   = result.pop("lab_date")
            print(f"Vendor : {vendor}")
            print(f"Date   : {date}")
            print()
            for k, v in result.items():
                flag = ""
                print(f"  {k:<22} {str(v) if v is not None else '—':>8}  {flag}")
        except Exception as e:
            print(f"ERROR: {e}")
