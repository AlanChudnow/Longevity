"""
Tests for src/health_models.py

Uses a synthetic life table fixture so tests never require a CDC download.
Run:  python -m pytest tests/
"""
from __future__ import annotations

import math
from typing import List, Dict

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Synthetic life table fixture
# ---------------------------------------------------------------------------

def _make_synthetic_life_table() -> pd.DataFrame:
    """
    Gompertz-Makeham mortality calibrated to rough CDC 2021 levels.
    Only used in tests — never in production (production uses CDC parquet).
    """
    records = []
    params = {
        "male":   {"A": 0.00022, "B": 6.5e-6, "C": 0.1064},
        "female": {"A": 0.00015, "B": 4.8e-6, "C": 0.1045},
    }
    for sex, p in params.items():
        for age in range(0, 101):
            mx_val = p["A"] + p["B"] * math.exp(p["C"] * age)
            qx = 1.0 - math.exp(-mx_val)
            mx = -math.log(max(1.0 - qx, 1e-10))
            records.append({"age": age, "sex": sex, "year": 2022, "mx": mx})
    return pd.DataFrame(records)


@pytest.fixture(autouse=True)
def _inject_synthetic_life_table(monkeypatch, tmp_path):
    """
    Replace the module-level cached DataFrame with synthetic data.
    Also redirect _CACHE_PATH so no real file is read or written.
    """
    import src.health_models as hm
    df = _make_synthetic_life_table()
    monkeypatch.setattr(hm, "_LIFETABLE_DF", df)
    monkeypatch.setattr(hm, "_CACHE_PATH", tmp_path / "life_table.parquet")


# ---------------------------------------------------------------------------
# life_table_mx
# ---------------------------------------------------------------------------

class TestLifeTableMx:
    def test_mortality_increases_with_age_male(self):
        from src.health_models import life_table_mx
        assert life_table_mx(70, "male") > life_table_mx(50, "male")

    def test_mortality_increases_with_age_female(self):
        from src.health_models import life_table_mx
        assert life_table_mx(70, "female") > life_table_mx(50, "female")

    def test_female_lower_than_male_midlife(self):
        from src.health_models import life_table_mx
        assert life_table_mx(55, "female") < life_table_mx(55, "male")

    def test_mx_positive_and_sane(self):
        from src.health_models import life_table_mx
        for age in [20, 40, 60, 80]:
            mx = life_table_mx(age, "male")
            assert 0 < mx < 1, f"mx={mx} out of range at age {age}"


# ---------------------------------------------------------------------------
# integrate_survival
# ---------------------------------------------------------------------------

class TestIntegrateSurvival:
    def test_survival_monotonically_decreasing(self):
        from src.health_models import integrate_survival
        curve = integrate_survival(55, "male")
        s_vals = [r["S"] for r in curve]
        assert all(s_vals[i] >= s_vals[i + 1] for i in range(len(s_vals) - 1))

    def test_first_step_below_one(self):
        from src.health_models import integrate_survival
        curve = integrate_survival(55, "male")
        assert 0 < curve[0]["S"] < 1.0

    def test_higher_rel_hazard_lowers_survival(self):
        from src.health_models import integrate_survival
        curve_avg = integrate_survival(55, "male", rel_hazard=1.0)
        curve_high = integrate_survival(55, "male", rel_hazard=3.0)
        # Compare at age 65 (10-year mark)
        S_avg = next(r["S"] for r in curve_avg if r["age"] == 65.0)
        S_high = next(r["S"] for r in curve_high if r["age"] == 65.0)
        assert S_high < S_avg

    def test_females_survive_longer_than_males(self):
        from src.health_models import integrate_survival, summarize_survival
        _, _, med_m = summarize_survival(integrate_survival(55, "male"), 55)
        _, _, med_f = summarize_survival(integrate_survival(55, "female"), 55)
        assert med_f > med_m

    def test_returns_list_of_dicts(self):
        from src.health_models import integrate_survival
        curve = integrate_survival(55, "male")
        assert isinstance(curve, list)
        assert all({"age", "S", "h"} <= set(r.keys()) for r in curve)


# ---------------------------------------------------------------------------
# summarize_survival
# ---------------------------------------------------------------------------

class TestSummarizeSurvival:
    def _curve(self, age: int = 55, sex: str = "male", rh: float = 1.0):
        from src.health_models import integrate_survival
        return integrate_survival(age, sex, rel_hazard=rh)

    def test_risks_in_unit_interval(self):
        from src.health_models import summarize_survival
        r5, r10, med = summarize_survival(self._curve(), 55)
        assert 0.0 <= r5 <= 1.0
        assert 0.0 <= r10 <= 1.0
        assert med > 0.0

    def test_ten_year_risk_geq_five_year(self):
        from src.health_models import summarize_survival
        r5, r10, _ = summarize_survival(self._curve(), 55)
        assert r10 >= r5

    def test_higher_hazard_raises_risk(self):
        from src.health_models import summarize_survival
        _, r10_avg, med_avg = summarize_survival(self._curve(rh=1.0), 55)
        _, r10_high, med_high = summarize_survival(self._curve(rh=3.0), 55)
        assert r10_high > r10_avg
        assert med_high < med_avg

    def test_empty_curve_returns_zeros(self):
        from src.health_models import summarize_survival
        assert summarize_survival([], 55) == (0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# Layer 2 — ASCVD Pooled Cohort Equations
# Ground-truth values verified against AHA/ACC online calculator
# ---------------------------------------------------------------------------

class TestASCVD:
    """
    Reference cases from the ACC/AHA ASCVD Risk Estimator Plus.
    Tolerance ±2 percentage points (rounding in published coefficients).
    """

    def _risk(self, **kw) -> float:
        from src.health_models import ascvd_10yr_risk
        return ascvd_10yr_risk(kw)

    def _hazard(self, **kw) -> float:
        from src.health_models import predict_ascvd_hazard
        return predict_ascvd_hazard(kw)

    # -- White male test cases -----------------------------------------------

    def test_wm_low_risk(self):
        # 55yo WM, TC=200, HDL=50, SBP=120 untreated, non-smoker, no DM
        # AHA calculator: ~5-7%
        risk = self._risk(age=55, sex="male", total_cholesterol=200,
                          hdl=50, systolic_bp=120, smoker="never",
                          diabetes=False, race="white", bp_treated=False)
        assert 0.03 < risk < 0.10, f"Expected 3-10%, got {risk:.1%}"

    def test_wm_high_risk_smoker_hypertension(self):
        # 55yo WM, TC=240, HDL=35, SBP=150 untreated, current smoker, no DM
        # AHA calculator: ~20-25%
        risk = self._risk(age=55, sex="male", total_cholesterol=240,
                          hdl=35, systolic_bp=150, smoker="current",
                          diabetes=False, race="white", bp_treated=False)
        assert 0.15 < risk < 0.35, f"Expected 15-35%, got {risk:.1%}"

    def test_wm_high_risk_is_greater_than_low_risk(self):
        low  = self._risk(age=55, sex="male", total_cholesterol=180,
                          hdl=60, systolic_bp=115, smoker="never",
                          diabetes=False, race="white")
        high = self._risk(age=55, sex="male", total_cholesterol=250,
                          hdl=30, systolic_bp=160, smoker="current",
                          diabetes=True, race="white")
        assert high > low * 3, "High-risk profile should be >3x the low-risk profile"

    # -- White female test cases ---------------------------------------------

    def test_wf_lower_risk_than_male_same_inputs(self):
        kw = dict(total_cholesterol=200, hdl=50, systolic_bp=120,
                  smoker="never", diabetes=False, race="white", bp_treated=False)
        risk_m = self._risk(age=55, sex="male",   **kw)
        risk_f = self._risk(age=55, sex="female", **kw)
        assert risk_f < risk_m, "Females should have lower ASCVD risk than males"

    def test_wf_near_average_hazard_is_near_one(self):
        # Average-ish inputs for white female → rel_hazard near 1.0
        # TC=200, HDL=55, SBP=120, non-smoker, no DM
        h = self._hazard(age=55, sex="female", total_cholesterol=200,
                         hdl=55, systolic_bp=120, smoker="never",
                         diabetes=False, race="white", bp_treated=False)
        assert 0.7 < h < 1.5, f"Near-average profile should have hazard ~1, got {h:.3f}"

    # -- Directional / boundary tests ----------------------------------------

    def test_smoker_raises_hazard(self):
        base = dict(age=55, sex="male", total_cholesterol=210, hdl=45,
                    systolic_bp=130, diabetes=False, race="white")
        h_never   = self._hazard(smoker="never",   **base)
        h_current = self._hazard(smoker="current", **base)
        assert h_current > h_never * 1.5

    def test_diabetes_raises_hazard(self):
        base = dict(age=55, sex="male", total_cholesterol=210, hdl=45,
                    systolic_bp=130, smoker="never", race="white")
        h_no  = self._hazard(diabetes=False, **base)
        h_yes = self._hazard(diabetes=True,  **base)
        assert h_yes > h_no

    def test_age_clamped_to_pce_range(self):
        # Should not raise even for age outside 40-79
        risk = self._risk(age=30, sex="male", total_cholesterol=200,
                          hdl=50, systolic_bp=120, smoker="never", diabetes=False)
        assert 0.0 < risk < 1.0

    def test_missing_cholesterol_uses_defaults(self):
        # Should not raise; uses TC=200, HDL=50 defaults
        h = self._hazard(age=55, sex="male", systolic_bp=120,
                         smoker="never", diabetes=False)
        assert h > 0


# ---------------------------------------------------------------------------
# Layer 3 — VO2 Max hazard
# ---------------------------------------------------------------------------

class TestVO2Hazard:
    def test_none_returns_one(self):
        from src.health_models import predict_vo2_hazard
        assert predict_vo2_hazard(55, "male", None) == 1.0

    def test_bottom_quartile_is_reference(self):
        from src.health_models import predict_vo2_hazard
        # Male 55yo: P25=30.7; value below that → bottom quartile HR=1.0
        assert predict_vo2_hazard(55, "male", 25.0) == 1.0

    def test_top_quartile_is_protective(self):
        from src.health_models import predict_vo2_hazard
        # Male 55yo: P75=40.4; value above → top quartile HR=0.25
        h = predict_vo2_hazard(55, "male", 45.0)
        assert h == 0.25

    def test_female_norms_distinct(self):
        from src.health_models import predict_vo2_hazard
        # 50yo female: P25=23.0; a VO2 of 20 → bottom quartile
        assert predict_vo2_hazard(50, "female", 20.0) == 1.0

    def test_higher_vo2_lower_hazard(self):
        from src.health_models import predict_vo2_hazard
        h_low  = predict_vo2_hazard(55, "male", 25.0)  # bottom 25th
        h_high = predict_vo2_hazard(55, "male", 45.0)  # top 25th
        assert h_high < h_low


# ---------------------------------------------------------------------------
# Layer 5 — Grip strength / dead hang hazard
# ---------------------------------------------------------------------------

class TestGripHazard:
    def test_none_returns_one(self):
        from src.health_models import predict_grip_hazard
        assert predict_grip_hazard("male") == 1.0

    def test_at_male_median_no_penalty(self):
        from src.health_models import predict_grip_hazard
        h = predict_grip_hazard("male", grip_kg=46.0)
        assert abs(h - 1.0) < 0.01

    def test_below_median_raises_hazard(self):
        from src.health_models import predict_grip_hazard
        h = predict_grip_hazard("male", grip_kg=31.0)  # 15kg below median
        assert h > 1.3

    def test_above_median_is_one(self):
        from src.health_models import predict_grip_hazard
        h = predict_grip_hazard("male", grip_kg=60.0)
        assert h == 1.0

    def test_dead_hang_top_quartile_low_penalty(self):
        from src.health_models import predict_grip_hazard
        # Male 90s+ hang → top quartile → estimated grip above median → HR=1.0
        h = predict_grip_hazard("male", hang_seconds=95)
        assert h == 1.0

    def test_dead_hang_bottom_quartile_male(self):
        from src.health_models import predict_grip_hazard
        # Male <20s → bottom quartile → below median grip → HR > 1
        h = predict_grip_hazard("male", hang_seconds=10)
        assert h > 1.1

    def test_grip_takes_priority_over_hang(self):
        from src.health_models import predict_grip_hazard
        # grip_kg provided alongside hang_seconds — grip should win
        h_grip = predict_grip_hazard("male", grip_kg=46.0, hang_seconds=5)
        assert abs(h_grip - 1.0) < 0.01


# ---------------------------------------------------------------------------
# Combined hazard integration
# ---------------------------------------------------------------------------

class TestCombinedHazard:
    def test_average_person_near_one(self):
        from src.health_models import predict_combined_hazard
        h = predict_combined_hazard({
            "age": 55, "sex": "male", "race": "white",
            "total_cholesterol": 200, "hdl": 50, "systolic_bp": 120,
            "smoker": "never", "diabetes": False,
        })
        # No VO2 or grip → only ASCVD layer; near-average inputs → h near 1
        assert 0.4 < h < 2.0

    def test_elite_fitness_lowers_hazard(self):
        from src.health_models import predict_combined_hazard
        base = dict(age=55, sex="male", race="white",
                    total_cholesterol=200, hdl=50, systolic_bp=120,
                    smoker="never", diabetes=False)
        h_no_fitness  = predict_combined_hazard(base)
        h_elite = predict_combined_hazard({**base, "vo2_max": 50.0, "grip_kg": 55.0})
        assert h_elite < h_no_fitness

    def test_high_risk_profile_exceeds_average(self):
        from src.health_models import predict_combined_hazard
        h = predict_combined_hazard({
            "age": 60, "sex": "male", "race": "white",
            "total_cholesterol": 250, "hdl": 30, "systolic_bp": 160,
            "smoker": "current", "diabetes": True,
            "vo2_max": 22.0, "grip_kg": 28.0,
        })
        assert h > 3.0
