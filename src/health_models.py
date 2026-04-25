"""
health_models.py — survival modeling with CDC life table baseline.

Layer 1: CDC life table baseline (mx).
Layer 2: ACC/AHA Pooled Cohort Equations (ASCVD).
Layer 3: VO2 Max percentile (Cooper Institute norms).
Layer 4: Autonomic — resting HR + HRV (added Phase 3+).
Layer 5: Grip strength / dead hang (Lancet PURE coefficients).
"""
from __future__ import annotations

import io
import math
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_PROJECT_DIR = Path(__file__).parent.parent
_DATA_DIR = _PROJECT_DIR / "data"
_CACHE_PATH = _DATA_DIR / "life_table.parquet"

# In-memory cache; populated by _load_life_table() or prime_life_table_cache()
_LIFETABLE_DF: Optional[pd.DataFrame] = None

# ---------------------------------------------------------------------------
# CDC source URLs — NVSR 74-06 (United States Life Tables, 2023)
# Table02 = males, Table03 = females; Table01 = total (fallback)
# ---------------------------------------------------------------------------
_CDC_BASE = "https://ftp.cdc.gov/pub/Health_Statistics/NCHS/Publications/NVSR/74-06"
_CDC_SOURCES = [
    ("male",   f"{_CDC_BASE}/Table02.xlsx", 2023),
    ("female", f"{_CDC_BASE}/Table03.xlsx", 2023),
]
_CDC_FALLBACK = (f"{_CDC_BASE}/Table01.xlsx", 2023)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ensure_data_dir() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)


def _fetch(url: str, timeout: int = 60) -> bytes:
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.content


def _extract_age(cell) -> Optional[int]:
    """
    Extract the starting age from a CDC life table age cell.
    Handles formats: integer 0, float 0.0, string "0–1" or "0-1".
    """
    if cell is None:
        return None
    if isinstance(cell, float) and math.isnan(cell):
        return None
    s = str(cell).strip()
    # Plain integer / float (e.g. "0", "55.0")
    try:
        return int(float(s))
    except ValueError:
        pass
    # Age-interval format: "0–1", "55–56" (en-dash or regular hyphen)
    m = re.match(r"^(\d+)", s)
    if m:
        return int(m.group(1))
    return None


def _parse_cdc_excel(content: bytes, sex: str, year: int) -> pd.DataFrame:
    """
    Parse a CDC NVSR life table Excel file into (age, sex, year, mx) rows.

    CDC tables have header rows before data, so we scan for the first row
    where column 0 represents age 0 and column 1 is a valid qx (0 < qx < 1).
    mx is computed from qx as  mx = -ln(1 - qx).
    """
    raw = pd.read_excel(io.BytesIO(content), header=None, engine="openpyxl")

    data_start: Optional[int] = None
    for i in range(len(raw)):
        age = _extract_age(raw.iat[i, 0])
        if age == 0:
            try:
                qx = float(raw.iat[i, 1])
                if 0 < qx < 1:
                    data_start = i
                    break
            except (ValueError, TypeError):
                pass

    if data_start is None:
        raise ValueError(
            f"Could not locate age-0 data row in life table Excel for sex='{sex}'"
        )

    records = []
    for i in range(data_start, len(raw)):
        age = _extract_age(raw.iat[i, 0])
        if age is None:
            break
        try:
            qx = float(raw.iat[i, 1])
        except (ValueError, TypeError):
            break
        if not (0 < qx <= 1):
            continue
        mx = -math.log(max(1.0 - qx, 1e-10))
        records.append({"age": age, "sex": sex, "year": year, "mx": mx})

    if not records:
        raise ValueError(f"No valid rows parsed from life table for sex='{sex}'")

    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def prime_life_table_cache(
    progress: Optional[Callable[[str], None]] = None,
) -> None:
    """
    Download CDC US Life Tables and persist to data/life_table.parquet.

    Tries male/female tables from NVSR 73-02 first; on failure falls back
    to the total-population Table01 (used for both sexes).

    progress(msg) is called with status strings if provided.
    """
    _ensure_data_dir()

    def log(msg: str) -> None:
        if progress:
            progress(msg)
        else:
            print(msg)

    frames: List[pd.DataFrame] = []

    try:
        for sex, url, year in _CDC_SOURCES:
            log(f"Downloading CDC life table — {sex} ({url.split('/')[-1]})...")
            content = _fetch(url)
            log(f"Parsing {sex} life table...")
            df = _parse_cdc_excel(content, sex=sex, year=year)
            frames.append(df)
            log(f"  {len(df)} age rows loaded for {sex}")
    except Exception as exc:
        log(f"Sex-specific tables failed ({exc}); trying fallback (total population)...")
        frames = []
        url, year = _CDC_FALLBACK
        content = _fetch(url)
        for sex in ("male", "female"):
            df = _parse_cdc_excel(content, sex=sex, year=year)
            frames.append(df)
            log(f"  fallback: {len(df)} rows for {sex}")

    combined = (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates(subset=["age", "sex", "year"])
        .sort_values(["year", "sex", "age"])
        .reset_index(drop=True)
    )

    combined.to_parquet(_CACHE_PATH, index=False)

    global _LIFETABLE_DF
    _LIFETABLE_DF = combined
    log(f"Life table cached at {_CACHE_PATH}  ({len(combined)} rows)")


def _load_life_table() -> pd.DataFrame:
    global _LIFETABLE_DF
    if _LIFETABLE_DF is not None:
        return _LIFETABLE_DF
    if not _CACHE_PATH.exists():
        raise FileNotFoundError(
            f"Life table cache not found at '{_CACHE_PATH}'.\n"
            "Call prime_life_table_cache() once to download from CDC."
        )
    df = pd.read_parquet(_CACHE_PATH)
    for col in ("age", "sex", "year", "mx"):
        if col not in df.columns:
            raise ValueError(f"Cached life table is missing column: {col}")
    _LIFETABLE_DF = df
    return df


def life_table_mx(
    age: int | float,
    sex: str,
    year: Optional[int] = None,
) -> float:
    """
    Return the baseline annual mortality rate (mx) for a given age and sex.
    Uses the most recent available year when year is None.
    """
    df = _load_life_table()
    age_i = int(age)
    sex = (sex or "").strip().lower()

    if year is None:
        year = int(df["year"].max())

    row = df[(df["year"] == year) & (df["sex"] == sex) & (df["age"] == age_i)]
    if not row.empty:
        return float(row.iloc[0]["mx"])

    # Fallback 1: nearest year with same sex + age
    sub = df[(df["sex"] == sex) & (df["age"] == age_i)].sort_values("year")
    if not sub.empty:
        return float(sub.iloc[-1]["mx"])

    # Fallback 2: nearest age in most recent year
    recent = df[(df["year"] == int(df["year"].max())) & (df["sex"] == sex)]
    if not recent.empty:
        idx = (recent["age"] - age_i).abs().idxmin()
        return float(recent.loc[idx, "mx"])

    return float(df["mx"].median())


def integrate_survival(
    age0: int,
    sex: str,
    rel_hazard: float = 1.0,
    age_max: int = 110,
) -> List[Dict[str, float]]:
    """
    Integrate the survival function from age0 using piecewise-constant hazards.

    rel_hazard multiplicatively scales the baseline mx at every age step.
    Returns a list of {age, S, h} dicts (S = cumulative survival probability).
    """
    df = _load_life_table()
    year = int(df["year"].max())
    sex = (sex or "").strip().lower()
    age0 = int(age0)

    S = 1.0
    curve: List[Dict[str, float]] = []
    for age in range(age0, age_max + 1):
        mx = life_table_mx(age, sex, year=year)
        h = mx * float(rel_hazard)
        S *= math.exp(-h)
        curve.append({"age": float(age), "S": float(S), "h": float(h)})
        if S < 1e-6:
            break
    return curve


def summarize_survival(
    curve: List[Dict[str, float]],
    age0: int,
) -> tuple[float, float, float]:
    """
    Compute 5-year risk, 10-year risk, and median remaining years.
    Returns (risk_5, risk_10, median_years).
    """
    if not curve:
        return 0.0, 0.0, 0.0

    S_by_age = {int(r["age"]): float(r["S"]) for r in curve}
    max_age = max(S_by_age)

    def S_at(delta: int) -> float:
        return S_by_age.get(int(age0) + delta, S_by_age[max_age])

    risk_5 = max(0.0, min(1.0, 1.0 - S_at(5)))
    risk_10 = max(0.0, min(1.0, 1.0 - S_at(10)))

    # Median: linear interpolation within the year S first crosses 0.5
    median_years: Optional[float] = None
    prev_age, prev_S = int(age0), 1.0
    for row in curve:
        if row["S"] <= 0.5:
            t0 = prev_age - int(age0)
            t1 = int(row["age"]) - int(age0)
            S0, S1 = prev_S, float(row["S"])
            frac = (0.5 - S0) / (S1 - S0) if S1 != S0 else 0.0
            median_years = t0 + max(0.0, min(1.0, frac)) * (t1 - t0)
            break
        prev_age = int(row["age"])
        prev_S = float(row["S"])

    if median_years is None:
        # Survival never drops to 50%; use area under curve (≈ mean LE)
        A = 0.0
        prev_a, prev_s = int(age0), 1.0
        for row in curve:
            A += 0.5 * (prev_s + float(row["S"])) * (int(row["age"]) - prev_a)
            prev_a = int(row["age"])
            prev_s = float(row["S"])
        median_years = A

    return float(risk_5), float(risk_10), float(median_years)


# ===========================================================================
# Layer 2 — ACC/AHA 2013 Pooled Cohort Equations
# Reference: Goff et al., JACC 2014 (doi:10.1016/j.jacc.2013.11.005)
# ===========================================================================

# Published coefficients, baseline survivals, and mean coefficient sums
# for each sex × race stratum. Source: Table A in Goff et al. 2014.
_PCE: Dict[str, Dict] = {
    "white_male": {
        "coef": {
            "ln_age":            12.344,
            "ln_tc":             11.853,
            "ln_age_ln_tc":      -2.664,
            "ln_hdl":            -7.990,
            "ln_age_ln_hdl":      1.769,
            "ln_sbp_treated":     1.797,
            "ln_sbp_untreated":   1.764,
            "smoker":             7.837,
            "ln_age_smoker":     -1.795,
            "diabetes":           0.661,
        },
        "mean_coef": 61.18,
        "baseline_s10": 0.9144,
    },
    "aa_male": {
        "coef": {
            "ln_age":            2.469,
            "ln_tc":             0.302,
            "ln_hdl":           -0.307,
            "ln_sbp_treated":    1.916,
            "ln_sbp_untreated":  1.809,
            "smoker":            0.549,
            "diabetes":          0.645,
        },
        "mean_coef": 19.54,
        "baseline_s10": 0.8954,
    },
    "white_female": {
        "coef": {
            "ln_age":            -29.799,
            "ln_age_sq":           4.884,
            "ln_tc":              13.540,
            "ln_age_ln_tc":       -3.114,
            "ln_hdl":            -13.578,
            "ln_age_ln_hdl":       3.149,
            "ln_sbp_treated":      2.019,
            "ln_sbp_untreated":    1.957,
            "smoker":              7.574,
            "ln_age_smoker":      -1.665,
            "diabetes":            0.661,
        },
        "mean_coef": -29.799,
        "baseline_s10": 0.9665,
    },
    "aa_female": {
        "coef": {
            "ln_age":             17.1141,
            "ln_tc":               0.9396,
            "ln_hdl":            -18.9196,
            "ln_age_ln_hdl":       4.4748,
            "ln_sbp_treated":     29.2907,
            "ln_age_ln_sbp_t":    -6.4321,
            "ln_sbp_untreated":   27.8197,
            "ln_age_ln_sbp_u":    -6.0873,
            "smoker":              0.8738,
            "diabetes":            0.8738,
        },
        "mean_coef": 86.6081,
        "baseline_s10": 0.9533,
    },
}


def _pce_individual_sum(
    age: float, total_chol: float, hdl: float, sbp: float,
    smoker: bool, diabetes: bool, bp_treated: bool,
    stratum: str,
) -> float:
    """Compute the individual linear predictor for one PCE stratum."""
    la = math.log(age)
    ltc = math.log(total_chol)
    lhdl = math.log(hdl)
    lsbp = math.log(sbp)
    sm = 1.0 if smoker else 0.0
    dm = 1.0 if diabetes else 0.0
    c = _PCE[stratum]["coef"]

    s = 0.0
    s += c.get("ln_age", 0) * la
    s += c.get("ln_age_sq", 0) * la * la
    s += c.get("ln_tc", 0) * ltc
    s += c.get("ln_age_ln_tc", 0) * la * ltc
    s += c.get("ln_hdl", 0) * lhdl
    s += c.get("ln_age_ln_hdl", 0) * la * lhdl

    if bp_treated:
        s += c.get("ln_sbp_treated", 0) * lsbp
        s += c.get("ln_age_ln_sbp_t", 0) * la * lsbp
    else:
        s += c.get("ln_sbp_untreated", 0) * lsbp
        s += c.get("ln_age_ln_sbp_u", 0) * la * lsbp

    s += c.get("smoker", 0) * sm
    s += c.get("ln_age_smoker", 0) * la * sm
    s += c.get("diabetes", 0) * dm
    return s


def predict_ascvd_hazard(features: Dict) -> float:
    """
    ACC/AHA Pooled Cohort Equations — returns relative hazard vs the mean
    PCE participant (1.0 = average risk for that sex/race stratum).

    Expected keys in features:
        age              : int/float   (PCE validated for 40–79)
        sex              : "male" | "female"
        total_cholesterol: float mg/dL
        hdl              : float mg/dL
        systolic_bp      : float mmHg
        smoker           : "never" | "former" | "current"
        diabetes         : bool
        race             : "white" | "aa"  (default "white")
        bp_treated       : bool            (default False)

    Missing cholesterol or HDL: defaults to average values (TC=200, HDL=50).
    """
    age = float(features.get("age") or 55)
    sex = (features.get("sex") or "male").strip().lower()
    tc = float(features.get("total_cholesterol") or 200)
    hdl = float(features.get("hdl") or 50)
    sbp = float(features.get("systolic_bp") or 120)
    race = (features.get("race") or "white").strip().lower()
    bp_treated = bool(features.get("bp_treated", False))
    diabetes = bool(features.get("diabetes", False))
    smoker_str = (features.get("smoker") or "never").strip().lower()
    smoker = smoker_str == "current"

    # Clamp age to PCE-validated range
    age = max(40.0, min(79.0, age))

    stratum = f"{'aa' if race == 'aa' else 'white'}_{sex}"
    if stratum not in _PCE:
        stratum = f"white_{sex}"  # fallback

    s = _pce_individual_sum(age, tc, hdl, sbp, smoker, diabetes, bp_treated, stratum)
    mean_coef = _PCE[stratum]["mean_coef"]
    return math.exp(s - mean_coef)


def ascvd_10yr_risk(features: Dict) -> float:
    """
    10-year ASCVD risk as a probability (0–1).
    Uses the published PCE baseline survival for the individual's stratum.
    """
    age = float(features.get("age") or 55)
    sex = (features.get("sex") or "male").strip().lower()
    race = (features.get("race") or "white").strip().lower()
    age = max(40.0, min(79.0, age))

    stratum = f"{'aa' if race == 'aa' else 'white'}_{sex}"
    if stratum not in _PCE:
        stratum = f"white_{sex}"

    s10 = _PCE[stratum]["baseline_s10"]
    rh = predict_ascvd_hazard(features)
    return max(0.0, min(1.0, 1.0 - s10 ** rh))


# ===========================================================================
# Layer 3 — VO2 Max fitness percentile
# Source: Cooper Institute norms; JAMA 2009 meta-analysis; Attia/JACC 2018
# ===========================================================================

# (age_min, age_max): (p25, p50, p75)  in ml/kg/min
_VO2_NORMS: Dict[str, List[tuple]] = {
    "male": [
        (20, 29, 38.3, 43.9, 49.5),
        (30, 39, 35.7, 41.0, 46.1),
        (40, 49, 33.3, 38.1, 43.4),
        (50, 59, 30.7, 34.9, 40.4),
        (60, 69, 26.7, 31.4, 36.1),
        (70, 99, 22.0, 27.0, 32.0),
    ],
    "female": [
        (20, 29, 31.6, 36.1, 41.1),
        (30, 39, 28.7, 33.1, 38.2),
        (40, 49, 25.9, 30.1, 35.3),
        (50, 59, 23.0, 27.2, 31.8),
        (60, 69, 20.1, 24.0, 28.7),
        (70, 99, 17.0, 21.0, 25.0),
    ],
}

# Hazard ratios by fitness quartile relative to bottom 25th (reference = 1.0)
_VO2_HR = {
    "bottom_25":  1.00,  # reference
    "p25_to_50":  0.50,  # 50% risk reduction
    "p50_to_75":  0.30,  # 70% risk reduction
    "top_25":     0.25,  # 75% risk reduction
}


def _vo2_percentile_bucket(age: float, sex: str, vo2: float) -> str:
    """Return the VO2 quartile bucket label for this individual."""
    sex = sex.strip().lower()
    norms = _VO2_NORMS.get(sex, _VO2_NORMS["male"])
    p25 = p50 = p75 = None
    for row in norms:
        if row[0] <= int(age) <= row[1]:
            p25, p50, p75 = row[2], row[3], row[4]
            break
    if p25 is None:  # age out of table — use oldest bracket
        _, _, p25, p50, p75 = norms[-1]
    if vo2 < p25:
        return "bottom_25"
    if vo2 < p50:
        return "p25_to_50"
    if vo2 < p75:
        return "p50_to_75"
    return "top_25"


def predict_vo2_hazard(
    age: float,
    sex: str,
    vo2_max: Optional[float],
) -> float:
    """
    VO2 Max fitness hazard modifier.
    Returns 1.0 (neutral) when vo2_max is None.
    """
    if vo2_max is None:
        return 1.0
    bucket = _vo2_percentile_bucket(age, sex, float(vo2_max))
    return _VO2_HR[bucket]


# ===========================================================================
# Layer 5 — Grip strength / dead hang
# Source: Lancet PURE study (Leong et al. 2015, n=142,861)
# ===========================================================================

# Sex-specific median grip strength (kg)
_GRIP_MEDIAN = {"male": 46.0, "female": 28.0}

# Dead hang percentile norms (seconds)
# Male: <20s bottom-25th, 20-45s 25-50th, 45-90s 50-75th, 90s+ top-25th
# Female: adjusted ~70% of male cutoffs (30% BW advantage)
_HANG_BUCKETS: Dict[str, List[tuple]] = {
    "male":   [(0, 20), (20, 45), (45, 90), (90, 9999)],
    "female": [(0, 14), (14, 32), (32, 63), (63, 9999)],
}

# Representative grip (fraction of median) for each hang quartile
_HANG_GRIP_FRAC = [0.68, 0.87, 1.05, 1.25]


def _grip_hr_from_kg(sex: str, grip_kg: float) -> float:
    """HR = 1.16 per 5 kg below sex-specific median; capped at 1.0 above median."""
    median = _GRIP_MEDIAN.get(sex.lower(), 46.0)
    deficit = max(0.0, median - grip_kg)
    return 1.16 ** (deficit / 5.0)


def predict_grip_hazard(
    sex: str,
    grip_kg: Optional[float] = None,
    hang_seconds: Optional[float] = None,
    weight_kg: Optional[float] = None,   # reserved for future BW-normalised norms
    age: Optional[float] = None,          # reserved for age-specific norms
) -> float:
    """
    Grip strength hazard modifier. Returns 1.0 when no input is supplied.

    Priority: grip_kg (dynamometer) > hang_seconds (dead hang).
    """
    sex = (sex or "male").strip().lower()

    if grip_kg is not None:
        return _grip_hr_from_kg(sex, float(grip_kg))

    if hang_seconds is not None:
        buckets = _HANG_BUCKETS.get(sex, _HANG_BUCKETS["male"])
        bucket_idx = len(buckets) - 1  # default to top if beyond all cutoffs
        for i, (lo, hi) in enumerate(buckets):
            if lo <= float(hang_seconds) < hi:
                bucket_idx = i
                break
        frac = _HANG_GRIP_FRAC[bucket_idx]
        estimated_grip = _GRIP_MEDIAN[sex] * frac
        return _grip_hr_from_kg(sex, estimated_grip)

    return 1.0  # neutral when no fitness data


# ===========================================================================
# Combined hazard (Layers 2 + 3 + 5; Layer 4 added when Apple Health wired)
# ===========================================================================

def predict_combined_hazard(features: Dict) -> float:
    """
    Multiply all available hazard layers. Missing inputs contribute HR = 1.0.

    features keys (all optional except age + sex for a useful result):
        age, sex, race, total_cholesterol, hdl, systolic_bp,
        smoker, diabetes, bp_treated,
        vo2_max,
        grip_kg, hang_seconds, weight_kg
    """
    age = float(features.get("age") or 55)
    sex = (features.get("sex") or "male").strip().lower()

    # Layer 2
    ascvd_h = predict_ascvd_hazard(features)

    # Layer 3
    vo2_h = predict_vo2_hazard(age, sex, features.get("vo2_max"))

    # Layer 5
    grip_h = predict_grip_hazard(
        sex,
        grip_kg=features.get("grip_kg"),
        hang_seconds=features.get("hang_seconds"),
        weight_kg=features.get("weight_kg"),
        age=age,
    )

    return ascvd_h * vo2_h * grip_h
