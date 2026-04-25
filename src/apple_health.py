"""
apple_health.py — Apple Health export.xml parser.

Uses iterparse for memory efficiency; export.xml can be several hundred MB.
Never raises on missing data — every field returns None when absent.

window parameter:
  "last_value"  — most recent single reading (default)
  "last_week"   — average of all readings in the past 7 days
  "last_month"  — average of all readings in the past 30 days

Fields where averaging makes sense (all dynamic biometrics):
  weight, systolic_bp, resting_hr, hrv, vo2_max, blood_glucose

Fields that always use the most recent single value:
  age (from DOB), sex, height (stable body measurement)
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

# ---------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------
_WANT = frozenset({
    "HKQuantityTypeIdentifierBodyMass",
    "HKQuantityTypeIdentifierHeight",
    "HKQuantityTypeIdentifierBloodPressureSystolic",
    "HKQuantityTypeIdentifierRestingHeartRate",
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN",
    "HKQuantityTypeIdentifierVO2Max",
    "HKQuantityTypeIdentifierBloodGlucose",
})

# Identifiers where cross-day averaging makes sense
_AVG_OK = frozenset({
    "HKQuantityTypeIdentifierBodyMass",
    "HKQuantityTypeIdentifierBloodPressureSystolic",
    "HKQuantityTypeIdentifierRestingHeartRate",
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN",
    "HKQuantityTypeIdentifierVO2Max",
    "HKQuantityTypeIdentifierBloodGlucose",
})

# Maps HK identifiers → output dict key (for _counts)
_ID_TO_KEY: Dict[str, str] = {
    "HKQuantityTypeIdentifierBodyMass":              "weight_lb",
    "HKQuantityTypeIdentifierBloodPressureSystolic": "systolic_bp",
    "HKQuantityTypeIdentifierRestingHeartRate":      "resting_hr",
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": "hrv",
    "HKQuantityTypeIdentifierVO2Max":                "vo2_max",
    "HKQuantityTypeIdentifierBloodGlucose":          "blood_glucose",
}

# ---------------------------------------------------------------------------
# Unit conversion
# ---------------------------------------------------------------------------

def _to_kg(v: float, unit: str) -> float:
    return v * 0.453592 if unit.lower() in ("lb", "lbs") else v

def _to_inches(v: float, unit: str) -> float:
    u = unit.lower()
    if u in ("cm", "cm^1"):
        return v / 2.54
    if u == "ft":
        return v * 12.0
    return v

def _to_mg_dl(v: float, unit: str) -> float:
    return v * 18.016 if unit.lower() == "mmol/l" else v

# ---------------------------------------------------------------------------
# Date / age helpers
# ---------------------------------------------------------------------------
_DATE_FMTS = (
    "%Y-%m-%d %H:%M:%S %z",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
)

def _parse_dt(s: str) -> Optional[datetime]:
    """Parse date string and return a timezone-naive datetime."""
    for fmt in _DATE_FMTS:
        try:
            dt = datetime.strptime(s.strip(), fmt)
            return dt.replace(tzinfo=None)   # normalize to naive for comparison
        except ValueError:
            pass
    return None

def _age_from_dob(dob_str: str) -> Optional[int]:
    try:
        dob = date.fromisoformat(dob_str[:10])
        today = date.today()
        return (today.year - dob.year
                - ((today.month, today.day) < (dob.month, dob.day)))
    except (ValueError, AttributeError, TypeError):
        return None

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_latest_biometrics(
    export_path: str,
    window: str = "last_value",
) -> Dict:
    """
    Parse Apple Health export.xml and return biometric values.

    window: "last_value" | "last_week" | "last_month"

    Returns dict with keys:
        age, sex, weight_lb, weight_kg, height_in, bmi,
        systolic_bp, resting_hr, hrv, vo2_max, blood_glucose,
        _counts  (maps field name → N readings averaged; empty for last_value)

    All metric values are None when not present in the export.
    Raises FileNotFoundError if the file does not exist.
    """
    path = Path(export_path)
    if not path.exists():
        raise FileNotFoundError(f"Apple Health export not found: {path}")

    # Cutoff for windowed modes (naive datetime)
    _now = datetime.now()
    if window == "last_week":
        _cutoff: Optional[datetime] = _now - timedelta(days=7)
    elif window == "last_month":
        _cutoff = _now - timedelta(days=30)
    else:
        _cutoff = None

    # latest[identifier] = (dt, raw_value, unit)  — most recent record
    latest: Dict[str, Tuple] = {}
    # windowed[identifier] = [(dt, raw_value, unit), ...]  — readings in window
    windowed: Dict[str, List[Tuple]] = {}

    dob_str: Optional[str] = None
    sex_raw: Optional[str] = None

    for _event, elem in ET.iterparse(str(path), events=("start",)):
        tag = elem.tag

        if tag == "Me":
            dob_str = elem.get("HKCharacteristicTypeIdentifierDateOfBirth")
            sex_raw = elem.get("HKCharacteristicTypeIdentifierBiologicalSex", "")

        elif tag == "Record":
            rec_type = elem.get("type", "")
            if rec_type in _WANT:
                end_str = elem.get("endDate", "")
                val_str = elem.get("value", "")
                unit    = elem.get("unit", "")
                dt = _parse_dt(end_str)
                try:
                    value = float(val_str)
                except (ValueError, TypeError):
                    elem.clear()
                    continue
                if dt is not None:
                    # Always track most-recent
                    prev = latest.get(rec_type)
                    if prev is None or dt > prev[0]:
                        latest[rec_type] = (dt, value, unit)
                    # Collect for windowed average
                    if _cutoff is not None and rec_type in _AVG_OK and dt >= _cutoff:
                        windowed.setdefault(rec_type, []).append((dt, value, unit))
            elem.clear()

    # ------------------------------------------------------------------
    # Build effective values — windowed average or latest
    # ------------------------------------------------------------------
    counts: Dict[str, int] = {}

    def _get(identifier: str) -> Optional[Tuple]:
        """
        Returns (dt, value, unit) for identifier.
        Uses windowed average when applicable; falls back to latest.
        Unit-converts before averaging so mixed-unit records average correctly.
        """
        if _cutoff is not None and identifier in _AVG_OK:
            readings = windowed.get(identifier, [])
            if readings:
                field_key = _ID_TO_KEY.get(identifier)
                # Convert to canonical units before averaging
                if identifier == "HKQuantityTypeIdentifierBodyMass":
                    vals = [_to_kg(r[1], r[2]) for r in readings]
                    canon_unit = "kg"
                elif identifier == "HKQuantityTypeIdentifierBloodGlucose":
                    vals = [_to_mg_dl(r[1], r[2]) for r in readings]
                    canon_unit = "mg/dL"
                else:
                    vals = [r[1] for r in readings]
                    canon_unit = readings[-1][2]
                avg = sum(vals) / len(vals)
                if field_key:
                    counts[field_key] = len(readings)
                return (readings[-1][0], avg, canon_unit)
        return latest.get(identifier)

    # Weight
    weight_kg: Optional[float] = None
    weight_lb: Optional[float] = None
    wt = _get("HKQuantityTypeIdentifierBodyMass")
    if wt:
        _, v, u = wt
        weight_kg = _to_kg(v, u)      # _get already converts to kg for windowed
        weight_lb = weight_kg / 0.453592

    # Height — always most recent; no averaging
    height_in: Optional[float] = None
    ht = latest.get("HKQuantityTypeIdentifierHeight")
    if ht:
        _, v, u = ht
        height_in = _to_inches(v, u)

    # BMI
    bmi: Optional[float] = None
    if weight_lb is not None and height_in is not None and height_in > 0:
        bmi = (weight_lb / height_in ** 2) * 703.0

    # Systolic BP
    systolic_bp: Optional[float] = None
    bp = _get("HKQuantityTypeIdentifierBloodPressureSystolic")
    if bp:
        systolic_bp = bp[1]

    # Resting HR
    resting_hr: Optional[float] = None
    rhr = _get("HKQuantityTypeIdentifierRestingHeartRate")
    if rhr:
        resting_hr = rhr[1]

    # HRV
    hrv: Optional[float] = None
    hrv_e = _get("HKQuantityTypeIdentifierHeartRateVariabilitySDNN")
    if hrv_e:
        hrv = hrv_e[1]

    # VO2 Max
    vo2_max: Optional[float] = None
    vo2 = _get("HKQuantityTypeIdentifierVO2Max")
    if vo2:
        vo2_max = vo2[1]

    # Blood glucose
    blood_glucose: Optional[float] = None
    bg = _get("HKQuantityTypeIdentifierBloodGlucose")
    if bg:
        _, v, u = bg
        blood_glucose = _to_mg_dl(v, u)   # already in mg/dL for windowed

    # Age + sex from <Me>
    age = _age_from_dob(dob_str) if dob_str else None
    sex: Optional[str] = None
    if sex_raw:
        s = sex_raw.lower()
        if "female" in s:
            sex = "female"
        elif "male" in s:
            sex = "male"

    def _r(v, digits=1):
        return round(v, digits) if v is not None else None

    return {
        "age":           age,
        "sex":           sex,
        "weight_lb":     _r(weight_lb, 1),
        "weight_kg":     _r(weight_kg, 2),
        "height_in":     _r(height_in, 1),
        "bmi":           _r(bmi, 1),
        "systolic_bp":   _r(systolic_bp, 0),
        "resting_hr":    _r(resting_hr, 0),
        "hrv":           _r(hrv, 1),
        "vo2_max":       _r(vo2_max, 1),
        "blood_glucose": _r(blood_glucose, 1),
        "_counts":       counts,
    }
