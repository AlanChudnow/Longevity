"""
Tests for src/apple_health.py

Uses synthetic XML fixtures in tests/fixtures/ — no Apple Health export needed.
Run:  python -m pytest tests/
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
SYNTHETIC = str(FIXTURES / "export_synthetic.xml")
EMPTY     = str(FIXTURES / "export_empty.xml")


@pytest.fixture(scope="module")
def bio():
    """Parse the synthetic fixture once for the whole module."""
    from src.apple_health import get_latest_biometrics
    return get_latest_biometrics(SYNTHETIC)


# ---------------------------------------------------------------------------
# Age and sex from <Me>
# ---------------------------------------------------------------------------

class TestMeElement:
    def test_sex_is_male(self, bio):
        assert bio["sex"] == "male"

    def test_age_is_correct(self, bio):
        # DOB 1970-06-15; age depends on today — compute dynamically
        dob = date(1970, 6, 15)
        today = date.today()
        expected = today.year - dob.year - (
            (today.month, today.day) < (dob.month, dob.day)
        )
        assert bio["age"] == expected

    def test_age_reasonable_range(self, bio):
        assert 50 <= bio["age"] <= 65


# ---------------------------------------------------------------------------
# Body mass — most-recent selection + unit conversion
# ---------------------------------------------------------------------------

class TestBodyMass:
    def test_selects_most_recent(self, bio):
        # Newer entry is 185 lb; older is 87 kg (~191 lb) — newer should win
        assert bio["weight_lb"] == pytest.approx(185.0, abs=0.5)

    def test_lb_to_kg_conversion(self, bio):
        # 185 lb → 83.91 kg
        assert bio["weight_kg"] == pytest.approx(185.0 * 0.453592, abs=0.05)

    def test_weight_kg_and_lb_consistent(self, bio):
        assert bio["weight_lb"] == pytest.approx(bio["weight_kg"] / 0.453592, abs=0.1)


# ---------------------------------------------------------------------------
# Height — cm to inches conversion
# ---------------------------------------------------------------------------

class TestHeight:
    def test_height_converted_from_cm(self, bio):
        # 177.8 cm → 70.0 in
        assert bio["height_in"] == pytest.approx(177.8 / 2.54, abs=0.1)


# ---------------------------------------------------------------------------
# BMI — derived from weight_lb + height_in
# ---------------------------------------------------------------------------

class TestBMI:
    def test_bmi_computed(self, bio):
        expected = (bio["weight_lb"] / bio["height_in"] ** 2) * 703.0
        assert bio["bmi"] == pytest.approx(expected, abs=0.2)

    def test_bmi_in_plausible_range(self, bio):
        assert 15.0 < bio["bmi"] < 45.0


# ---------------------------------------------------------------------------
# Lab + physiological fields
# ---------------------------------------------------------------------------

class TestPhysiologicalFields:
    def test_systolic_bp(self, bio):
        assert bio["systolic_bp"] == pytest.approx(125.0, abs=1)

    def test_resting_hr(self, bio):
        assert bio["resting_hr"] == pytest.approx(62.0, abs=1)

    def test_hrv(self, bio):
        assert bio["hrv"] == pytest.approx(45.2, abs=0.2)

    def test_vo2_max(self, bio):
        assert bio["vo2_max"] == pytest.approx(42.5, abs=0.2)

    def test_blood_glucose_mg_dl(self, bio):
        # Most recent entry is 95 mg/dL (2024-06-01); older mmol/L entry ignored
        assert bio["blood_glucose"] == pytest.approx(95.0, abs=1)


# ---------------------------------------------------------------------------
# Unit conversion edge cases
# ---------------------------------------------------------------------------

class TestUnitConversion:
    def test_mmol_l_glucose_converts(self, tmp_path):
        xml = tmp_path / "glucose_mmol.xml"
        xml.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<HealthData locale="en_US">
  <Record type="HKQuantityTypeIdentifierBloodGlucose"
          unit="mmol/L" value="5.5"
          startDate="2024-06-01 07:00:00 -0500"
          endDate="2024-06-01 07:00:00 -0500"/>
</HealthData>""", encoding="utf-8")
        from src.apple_health import get_latest_biometrics
        result = get_latest_biometrics(str(xml))
        # 5.5 mmol/L × 18.016 = 99.1 mg/dL
        assert result["blood_glucose"] == pytest.approx(5.5 * 18.016, abs=0.5)

    def test_ft_height_converts_to_inches(self, tmp_path):
        xml = tmp_path / "height_ft.xml"
        xml.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<HealthData locale="en_US">
  <Record type="HKQuantityTypeIdentifierHeight"
          unit="ft" value="5.66667"
          startDate="2024-06-01 08:00:00 -0500"
          endDate="2024-06-01 08:00:00 -0500"/>
</HealthData>""", encoding="utf-8")
        from src.apple_health import get_latest_biometrics
        result = get_latest_biometrics(str(xml))
        # 5.66667 ft × 12 = 68.0 in (5'8")
        assert result["height_in"] == pytest.approx(68.0, abs=0.1)

    def test_lb_weight_converts_to_kg(self, tmp_path):
        xml = tmp_path / "weight_lb.xml"
        xml.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<HealthData locale="en_US">
  <Record type="HKQuantityTypeIdentifierBodyMass"
          unit="lb" value="200.0"
          startDate="2024-06-01 08:00:00 -0500"
          endDate="2024-06-01 08:00:00 -0500"/>
</HealthData>""", encoding="utf-8")
        from src.apple_health import get_latest_biometrics
        result = get_latest_biometrics(str(xml))
        assert result["weight_kg"] == pytest.approx(200.0 * 0.453592, abs=0.05)


# ---------------------------------------------------------------------------
# Empty export and missing fields
# ---------------------------------------------------------------------------

class TestMissingData:
    def test_empty_export_all_none(self):
        from src.apple_health import get_latest_biometrics
        result = get_latest_biometrics(EMPTY)
        for key in ("age", "sex", "weight_lb", "weight_kg", "height_in", "bmi",
                    "systolic_bp", "resting_hr", "hrv", "vo2_max", "blood_glucose"):
            assert result[key] is None, f"Expected {key} to be None"

    def test_returns_all_expected_keys(self, bio):
        expected_keys = {"age", "sex", "weight_lb", "weight_kg", "height_in",
                         "bmi", "systolic_bp", "resting_hr", "hrv",
                         "vo2_max", "blood_glucose", "_counts"}
        assert expected_keys == set(bio.keys())


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrors:
    def test_file_not_found(self):
        from src.apple_health import get_latest_biometrics
        with pytest.raises(FileNotFoundError):
            get_latest_biometrics("C:/does/not/exist/export.xml")

    def test_female_sex_parsed(self, tmp_path):
        xml = tmp_path / "female.xml"
        xml.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<HealthData locale="en_US">
  <Me HKCharacteristicTypeIdentifierBiologicalSex="HKBiologicalSexFemale"/>
</HealthData>""", encoding="utf-8")
        from src.apple_health import get_latest_biometrics
        result = get_latest_biometrics(str(xml))
        assert result["sex"] == "female"
