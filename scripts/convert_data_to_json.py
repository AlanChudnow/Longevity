"""
scripts/convert_data_to_json.py — one-time data preparation for the web app.

Produces:
  data/life_table.json          — CDC life table (most recent year only)
  data/zip_life_expectancy.json — ZIP-level LE offset from USALEEP + Census crosswalk
  data/vo2_norms.json           — VO2 Max percentile tables by age/sex

Run once before deploying. Requires: pandas, pyarrow, requests.
"""
from __future__ import annotations

import io
import json
import re
import sys
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"


# ──────────────────────────────────────────────────────────────────────────────
# 1. life_table.json
# ──────────────────────────────────────────────────────────────────────────────

def convert_life_table():
    parquet = DATA / "life_table.parquet"
    if not parquet.exists():
        print("  ERROR: data/life_table.parquet not found — run prime_life_table_cache() first")
        return

    df = pd.read_parquet(parquet)
    most_recent_year = int(df["year"].max())
    df = df[df["year"] == most_recent_year].copy()
    print(f"  Life table: {len(df)} rows for year {most_recent_year}")

    records = [
        {"age": int(r["age"]), "sex": str(r["sex"]), "year": int(r["year"]), "mx": float(r["mx"])}
        for _, r in df.iterrows()
    ]
    out = DATA / "life_table.json"
    out.write_text(json.dumps(records, separators=(",", ":")), encoding="utf-8")
    print(f"  Written: {out}  ({out.stat().st_size // 1024} KB)")


# ──────────────────────────────────────────────────────────────────────────────
# 2. zip_life_expectancy.json — USALEEP + Census ZCTA crosswalk
# ──────────────────────────────────────────────────────────────────────────────

_USALEEP_DIR = "https://ftp.cdc.gov/pub/Health_Statistics/NCHS/Datasets/NVSS/USALEEP/CSV/"
_ZCTA_URL    = "https://www2.census.gov/geo/docs/maps-data/data/rel/zcta_county_rel_10.txt"
_NAMES_URL   = "https://www2.census.gov/geo/docs/reference/codes/files/national_county.txt"

_STATE_FIPS: dict[str, str] = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA",
    "08": "CO", "09": "CT", "10": "DE", "11": "DC", "12": "FL",
    "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN",
    "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME",
    "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS",
    "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
    "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND",
    "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI",
    "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT",
    "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI",
    "56": "WY",
}


def _fetch(url: str, timeout: int = 60) -> bytes:
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.content


def _list_state_csvs() -> list[str]:
    """Scrape directory listing for *_A.CSV filenames."""
    html = _fetch(_USALEEP_DIR, timeout=30).decode("utf-8", errors="replace")
    return sorted(re.findall(r'([A-Z]{2}_A\.CSV)', html))


def _download_usaleep_tract_le() -> pd.DataFrame:
    """Download all state _A.CSV files and return a DataFrame with tract_id + le columns."""
    state_files = _list_state_csvs()
    print(f"  Found {len(state_files)} state USALEEP files")
    frames = []
    for i, fname in enumerate(state_files):
        url = _USALEEP_DIR + fname
        try:
            raw = _fetch(url, timeout=30)
            df = pd.read_csv(io.BytesIO(raw), dtype=str)
            # Columns: Tract ID (11 digits), State FIPS, County FIPS, Life Expectancy, SE, Flag
            # Column names vary — use positional
            df.columns = [c.strip() for c in df.columns]
            # Columns: Tract ID, STATE2KX, CNTY2KX, TRACT2KX, e(0), se(e(0)), flag
            c0 = df.columns[0]   # Tract ID
            c1 = "e(0)"          # life expectancy at birth
            if c1 not in df.columns:
                c1 = df.columns[4]
            df = df[[c0, c1]].rename(columns={c0: "tract_id", c1: "le"})
            df["le"] = pd.to_numeric(df["le"], errors="coerce")
            df = df.dropna(subset=["le"])
            df["tract_id"] = df["tract_id"].astype(str).str.strip().str.zfill(11)
            frames.append(df)
            sys.stdout.write(f"\r  Downloaded {i+1}/{len(state_files)}: {fname}    ")
            sys.stdout.flush()
        except Exception as exc:
            print(f"\n  WARNING: Could not download {fname}: {exc}")
        time.sleep(0.05)  # be gentle to CDC FTP
    print()
    combined = pd.concat(frames, ignore_index=True)
    combined["county_fips"] = combined["tract_id"].str[:5]
    return combined


def convert_zip_life_expectancy():
    out = DATA / "zip_life_expectancy.json"

    # ── USALEEP tract-level → county averages ──────────────────────────────
    print("  Downloading USALEEP state files…")
    usaleep = _download_usaleep_tract_le()
    county_le = (
        usaleep.groupby("county_fips")["le"]
        .mean()
        .reset_index()
        .rename(columns={"le": "county_le"})
    )
    print(f"  {len(county_le)} counties with LE data")

    # ── Census ZCTA→county crosswalk ───────────────────────────────────────
    print("  Downloading Census ZCTA crosswalk…")
    zcta_raw = _fetch(_ZCTA_URL, timeout=60)
    zcta = pd.read_csv(io.BytesIO(zcta_raw), dtype=str)
    zcta.columns = [c.strip().upper() for c in zcta.columns]
    zcta["zip"]         = zcta["ZCTA5"].str.strip().str.zfill(5)
    zcta["county_fips"] = (zcta["STATE"].str.strip().str.zfill(2) +
                            zcta["COUNTY"].str.strip().str.zfill(3))
    zcta["state_fips"]  = zcta["STATE"].str.strip().str.zfill(2)
    pct_col = next((c for c in zcta.columns if "COPOPPCT" in c), None)
    if pct_col:
        zcta[pct_col] = pd.to_numeric(zcta[pct_col], errors="coerce").fillna(0)
        zcta_primary = (
            zcta.sort_values(pct_col, ascending=False)
                .groupby("zip", sort=False).first()
                .reset_index()[["zip", "county_fips", "state_fips"]]
        )
    else:
        zcta_primary = (
            zcta.groupby("zip", sort=False).first()
                .reset_index()[["zip", "county_fips", "state_fips"]]
        )
    print(f"  {len(zcta_primary)} ZIP codes in crosswalk")

    # ── County names ───────────────────────────────────────────────────────
    county_names: dict[str, str] = {}
    county_states: dict[str, str] = {}
    try:
        print("  Downloading county names…")
        names_raw = _fetch(_NAMES_URL, timeout=30)
        names_df = pd.read_csv(
            io.BytesIO(names_raw), header=None,
            names=["state_abbr", "sfips", "cfips", "county_name", "cls"],
            dtype=str,
        )
        names_df["county_fips"] = (names_df["sfips"].str.strip().str.zfill(2) +
                                    names_df["cfips"].str.strip().str.zfill(3))
        county_names  = dict(zip(names_df["county_fips"], names_df["county_name"]))
        county_states = dict(zip(names_df["county_fips"], names_df["state_abbr"].str.strip()))
        print(f"  {len(county_names)} county names loaded")
    except Exception as exc:
        print(f"  WARNING: County names unavailable ({exc})")

    # ── Assemble ───────────────────────────────────────────────────────────
    result = zcta_primary.merge(county_le, on="county_fips", how="left")
    result = result.dropna(subset=["county_le"])
    result["state_abbr"]  = result["county_fips"].map(county_states)
    result["county_name"] = result["county_fips"].map(county_names).fillna("")

    # Fill state_abbr from FIPS map where county name lookup missed
    def _fips_to_state(row):
        if row["state_abbr"] and not pd.isna(row["state_abbr"]):
            return row["state_abbr"]
        return _STATE_FIPS.get(row["state_fips"], "")
    result["state_abbr"] = result.apply(_fips_to_state, axis=1)

    national_mean = float(result["county_le"].mean())
    result["geo_mx_offset"] = result["county_le"] - national_mean
    result = result.rename(columns={"county_le": "life_expectancy"})

    final = result[["zip", "county_fips", "life_expectancy",
                     "geo_mx_offset", "county_name", "state_abbr"]].copy()
    final["zip"] = final["zip"].str.zfill(5)
    final = final.sort_values("zip").reset_index(drop=True)

    print(f"  {len(final)} ZIP codes  (national mean LE {national_mean:.1f} yrs)")

    # Verify required test ZIP
    test_zip = final[final["zip"] == "85268"]
    if not test_zip.empty:
        row = test_zip.iloc[0]
        print(f"  ZIP 85268 check: {row['county_name']}, {row['state_abbr']} "
              f"  LE={row['life_expectancy']:.1f}  offset={row['geo_mx_offset']:.2f}")
    else:
        print("  WARNING: ZIP 85268 not found in output!")

    records = [
        {
            "zip":             str(r["zip"]),
            "county_fips":    str(r["county_fips"]),
            "life_expectancy": round(float(r["life_expectancy"]), 2),
            "geo_mx_offset":   round(float(r["geo_mx_offset"]), 4),
            "county_name":     str(r["county_name"] or ""),
            "state_abbr":      str(r["state_abbr"] or ""),
        }
        for _, r in final.iterrows()
    ]
    out.write_text(json.dumps(records, separators=(",", ":")), encoding="utf-8")
    print(f"  Written: {out}  ({out.stat().st_size // 1024} KB)")


# ──────────────────────────────────────────────────────────────────────────────
# 3. vo2_norms.json — extracted from health_models.py constants
# ──────────────────────────────────────────────────────────────────────────────

_VO2_NORMS_RAW = {
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

_VO2_HAZARD_RATIOS = {
    "bottom_25": 1.00,
    "p25_to_50": 0.50,
    "p50_to_75": 0.30,
    "top_25":    0.25,
}


def convert_vo2_norms():
    data = {
        "norms": {
            sex: [
                {"age_min": t[0], "age_max": t[1], "p25": t[2], "p50": t[3], "p75": t[4]}
                for t in brackets
            ]
            for sex, brackets in _VO2_NORMS_RAW.items()
        },
        "hazard_ratios": _VO2_HAZARD_RATIOS,
    }
    out = DATA / "vo2_norms.json"
    out.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    print(f"  Written: {out}  ({out.stat().st_size} bytes)")


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    DATA.mkdir(exist_ok=True)

    print("\n[1/3] Converting life_table.parquet -> life_table.json")
    convert_life_table()

    print("\n[2/3] Building zip_life_expectancy.json (downloads from CDC + Census)...")
    convert_zip_life_expectancy()

    print("\n[3/3] Writing vo2_norms.json")
    convert_vo2_norms()

    print("\nDone. Validate files:")
    for f in ["life_table.json", "zip_life_expectancy.json", "vo2_norms.json"]:
        p = DATA / f
        print(f"  {'OK' if p.exists() else 'MISSING'}  {p}  "
              f"({p.stat().st_size // 1024} KB)" if p.exists() else f"  MISSING  {p}")
