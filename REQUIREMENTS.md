# Longevity Risk Calculator — Requirements & Development Plan

## Project Overview

A personal longevity prediction desktop app that auto-fills biometrics from Apple
Health, accepts blood panel data via PDF drag-and-drop or manual entry, and
produces a survival curve with risk callouts. Built in Python with a GUI that
matches the provided HTML mockup design.

## Directory Structure

```
C:\Users\Daddy\Apps\Longevity\
├── CLAUDE.md                  ← persistent Claude Code instructions
├── REQUIREMENTS.md            ← this file
├── requirements.txt           ← Python dependencies
├── main.py                    ← entry point
├── src/
│   ├── apple_health.py        ← parse export.xml → biometrics dict
│   ├── lab_parser.py          ← PDF/image → cholesterol via Claude Vision API
│   ├── health_models.py       ← survival math (life table + hazard model)
│   └── gui.py                 ← main application window
├── data/
│   └── life_table.parquet     ← CDC life table cache (created on first run)
└── design/
    └── Longevity_Risk_Calculator_v2.html  ← reference mockup from Claude Design
```

---

## Data Sources

| Source | Path | Notes |
|--------|------|-------|
| Apple Health export | `C:\Users\Daddy\Downloads\export.xml` | Auto-loaded on startup |
| GUI mockup | `C:\Users\Daddy\Apps\Longevity\design\Longevity_Risk_Calculator_v2.html` | Visual reference for layout and styling |

---

## Complete Input Specification

### Auto-filled from Apple Health (read-only in UI, with status indicator)

| Field | Apple Health Identifier | Notes |
|-------|------------------------|-------|
| Age | Date of birth in profile | Computed from DOB |
| Sex | Biological sex in profile | "male" or "female" |
| Weight (lb) | `HKQuantityTypeIdentifierBodyMass` | Most recent value |
| Height (in) | `HKQuantityTypeIdentifierHeight` | Most recent value |
| BMI | Computed | weight_lb / height_in² × 703 |
| Systolic BP | `HKQuantityTypeIdentifierBloodPressureSystolic` | Most recent value |
| Resting Heart Rate | `HKQuantityTypeIdentifierRestingHeartRate` | Most recent value |
| HRV | `HKQuantityTypeIdentifierHeartRateVariabilitySDNN` | Most recent value |
| VO2 Max | `HKQuantityTypeIdentifierVO2Max` | From any source (manual, Fitbit, etc.) |
| Blood Glucose | `HKQuantityTypeIdentifierBloodGlucose` | Used as diabetes proxy |

Each field shows:
- 🟢 Green indicator — value found and loaded
- 🟡 Amber indicator — not found, field left blank for manual entry

### Lab Panel (PDF drop or manual entry)

| Field | Unit | Notes |
|-------|------|-------|
| Total Cholesterol | mg/dL | |
| HDL | mg/dL | |
| LDL | mg/dL | |
| ApoB | mg/dL | Preferred over LDL where available |

Lab drag-and-drop zone: accepts PDF or image files. On drop, sends to Claude
Vision API with a structured extraction prompt. Returns JSON of available values.
Populates fields automatically; user can override any value manually.

### Manual Entry (always required)

| Field | Type | Options |
|-------|------|---------|
| Smoking status | Dropdown | never / former / current |
| Diabetes | Checkbox | Yes / No |

### Fitness Tests (optional, either or both accepted)

| Field | Unit | Notes |
|-------|------|-------|
| Grip strength | kg | From hand dynamometer |
| Dead hang time | seconds | Bodyweight loaded from Apple Health |

Both fields shown even when filled, with a note:
> "Improving these scores extends your curve."

If only dead hang time is provided (no dynamometer), derive grip strength
percentile from hang time normalized by bodyweight using published age/sex norms.
If dynamometer reading is provided, use it directly.

---

## Model Architecture

### Layer 1 — Baseline mortality
- Source: CDC US Life Tables
- Implementation: cached Parquet at `./data/life_table.parquet`
- Schema: `(age: int, sex: str, year: int, mx: float)`
- On first run: download from CDC and prime cache via `prime_life_table_cache()`
- API: `life_table_mx(age, sex, year=None) → float`

### Layer 2 — Cardiovascular risk (replaces toy model)
- **Model**: ACC/AHA Pooled Cohort Equations (2013)
- **Inputs**: age, sex, race (default "white" if unknown), total cholesterol,
  HDL, systolic BP, diabetes, smoking, BP treatment (default False)
- **Output**: 10-year ASCVD risk score → converted to relative hazard
- **Reference**: Goff et al., JACC 2014 — published coefficients, implement directly

### Layer 3 — Fitness adjustment
- **VO2 Max**: Apply published hazard ratios by fitness percentile
  - Bottom 25th: HR = 1.0 (reference)
  - 25th–50th: HR = 0.50 (50% risk reduction)
  - 50th–75th: HR = 0.30 (70% risk reduction)
  - Top 25th: HR = 0.25 (75% risk reduction)
  - Percentile derived from age/sex VO2 Max norms (Cooper Institute tables)
- **Source**: JAMA 2009 meta-analysis; Attia / JACC 2018 data

### Layer 4 — Autonomic adjustment
- **Resting HR**: HR modifier per 10 bpm above 60 (reference): +0.09 per unit
- **HRV**: Protective modifier; use age/sex percentile → small hazard adjustment
- **Source**: published cohort data, conservative coefficients

### Layer 5 — Grip strength adjustment
- **Dynamometer input**: HR = 1.16 per 5 kg reduction below sex-specific median
  - Male median: ~46 kg; Female median: ~28 kg
  - Source: Lancet PURE study (142,000 participants)
- **Dead hang input**: Convert to grip percentile using age/sex norms, then apply
  same HR table as dynamometer
  - Male norms: <20s = bottom 25th, 20–45s = 25–50th, 45–90s = 50–75th, 90s+ = top 25th
  - Female norms: adjust for ~30% lower bodyweight advantage

### Combining layers
```python
rel_hazard = ascvd_hazard * vo2_hazard * autonomic_hazard * grip_hazard
curve = integrate_survival(age, sex, rel_hazard)
```

Each layer multiplies independently. All layers optional except ASCVD — if
inputs are missing for a layer, that layer contributes HR = 1.0 (neutral).

### Population comparison curve
Run `integrate_survival(age, sex, rel_hazard=1.0)` for the gray reference curve.
This represents an average person of the same age and sex with no risk adjustments.

---

## GUI Specification

### Reference
Match the visual design from `design/Longevity_Risk_Calculator_v2.html` as
closely as possible given Python GUI constraints. Use CustomTkinter for modern
styling (not standard Tkinter).

### Layout — Single window, two sections stacked vertically

#### TOP SECTION — Inputs (three panels side by side)

**Panel 1 — Apple Health** (left third)
- Header: "Apple Health" with connection status badge
- Read-only fields with green/amber indicator dots
- Fields: Age, Sex, Weight, Height, BMI, Systolic BP, Resting HR, HRV, VO2 Max
- Small text at bottom: last loaded timestamp

**Panel 2 — Lab Results** (center third)
- Drag-and-drop zone at top: "Drop blood panel PDF here"
  - Accept .pdf and image files (.png, .jpg)
  - On drop: call lab_parser.py → populate fields below
  - Show "Extracting..." spinner while processing
- Manual entry fields below drop zone: Total Cholesterol, HDL, LDL, ApoB
- All fields editable regardless of whether PDF was dropped

**Panel 3 — Manual & Fitness** (right third)
- Smoking status dropdown: never / former / current
- Diabetes checkbox
- Divider line
- Grip Strength field (kg) with label "Dynamometer reading"
- Dead Hang Time field (seconds) with label "Hang to failure"
- Note text (muted): "Improving these scores extends your curve."

**Calculate button** — full width, below all three panels, accent blue

#### BOTTOM SECTION — Results

- **Survival curve chart** (main, large)
  - X axis: Age (from current age to 100)
  - Y axis: Survival probability 0–100%
  - Your curve: accent blue line
  - Population average: gray dashed line, same age/sex
  - Legend in top-right corner
  - Rendered with matplotlib embedded in the GUI

- **Three callout cards** below the chart (horizontal row)
  - 5-year risk %
  - 10-year risk %
  - Median remaining years
  - Monospace font for numbers (JetBrains Mono or similar)

- **Disclaimer** (small, muted)
  - "Not for clinical use. Model uses published epidemiological coefficients."

### Aesthetic
- White background, muted blue accent (#4A6FA5 or equivalent)
- Clean, no decorative elements
- Inter font or system sans-serif
- Section headers in medium weight, uppercase tracking
- Status indicators: filled circles, green (#3D9970) / amber (#F39C12)

---

## Lab PDF Extraction (lab_parser.py)

```python
def extract_lab_values(file_path: str) -> dict:
    """
    Send PDF or image to Claude Vision API.
    Returns dict with keys: total_cholesterol, hdl, ldl, apob
    Missing values returned as None.
    """
```

API call details:
- Model: `claude-opus-4-7` (vision capable)
- Encode file as base64
- System prompt: "You are a medical data extractor. Return only JSON."
- User prompt: "Extract these values from the lab report if present:
  total_cholesterol_mg_dl, hdl_mg_dl, ldl_mg_dl, apob_mg_dl.
  Return only a JSON object with these exact keys. Use null for missing values."
- Parse JSON response, return dict

---

## Apple Health Parser (apple_health.py)

```python
def get_latest_biometrics(export_path: str) -> dict:
    """
    Parse export.xml and return most recent value for each relevant metric.
    Returns dict with keys matching model feature names.
    Missing fields returned as None (never raises on missing data).
    """
```

Implementation notes:
- Use `xml.etree.ElementTree` iterparse for memory efficiency (export.xml can
  be very large — hundreds of MB)
- For each identifier, track the record with the most recent `endDate`
- Return values in model-native units (kg for weight, inches for height, etc.)
- Extract DOB from `<Me>` element for age calculation
- Extract biological sex from `<Me>` element

---

## Life Table Setup (first run)

On startup, check if `./data/life_table.parquet` exists. If not:
1. Download CDC US Life Tables from:
   `https://ftp.cdc.gov/pub/Health_Statistics/NCHS/Publications/NVSR/73_02/Table01.xlsx`
   (or nearest available year)
2. Parse and normalize to schema: `(age, sex, year, mx)`
3. Call `prime_life_table_cache()` to persist
4. Show progress dialog during download

---

## Python Dependencies (requirements.txt)

```
customtkinter>=5.2.0
matplotlib>=3.8.0
pandas>=2.0.0
pyarrow>=14.0.0
anthropic>=0.25.0
pillow>=10.0.0
requests>=2.31.0
openpyxl>=3.1.0
tkinterdnd2>=0.3.0
```

---

## CLAUDE.md Contents (persistent instructions for future sessions)

```markdown
# Longevity Risk Calculator — Claude Code Instructions

## Architecture rules
- ALL survival math stays in src/health_models.py — never in gui.py
- ALL Apple Health parsing stays in src/apple_health.py
- ALL lab PDF extraction stays in src/lab_parser.py
- gui.py only calls functions from the above modules, never implements logic

## Key paths
- Apple Health: C:\Users\Daddy\Downloads\export.xml
- Design mockup: design\Longevity_Risk_Calculator_v2.html
- Life table cache: data\life_table.parquet

## Model layers (in order, all multiplicative)
1. CDC life table baseline (mx)
2. ASCVD Pooled Cohort Equations → relative hazard
3. VO2 Max percentile → hazard modifier
4. HRV + resting HR → autonomic modifier
5. Grip strength / dead hang → muscle modifier

## Never do
- Never use the toy predict_relative_hazard() from the original prototype
- Never hardcode mortality rates — always use life table
- Never block the GUI thread during API calls (use threading)

## GUI framework
- CustomTkinter (not standard Tkinter)
- Match design/Longevity_Risk_Calculator_v2.html for layout and color
- Single window, inputs top, results bottom

## Testing
- After any change to health_models.py, run: python -m pytest tests/
- Keep a test fixture with known ASCVD inputs and expected outputs
```

---

## Incremental Development Plan

Each phase is independently runnable and testable before moving to the next.
All layers should include automated testing. Perform regression testing. 
Create the capability for me to run the automated tests.

---

### Phase 1 — Project scaffold and life table
**Goal**: Runnable app that loads life table and shows a placeholder survival curve.

Tasks:
1. Create directory structure
2. Write `requirements.txt` and install deps
3. Write `CLAUDE.md`
4. Download CDC life table, implement `prime_life_table_cache()` and
   `life_table_mx()` in `health_models.py`
5. Implement `integrate_survival()` and `summarize_survival()` using life table
6. Write `main.py` that calls the above and prints a test curve to console
7. Confirm: `python main.py` prints survival curve for a 55-year-old male

**Acceptance**: Console output shows age/survival pairs, no import errors.

---

### Phase 2 — ASCVD hazard model
**Goal**: Replace toy model with Pooled Cohort Equations.

Tasks:
1. Implement `predict_ascvd_hazard(features) → float` in `health_models.py`
   using published 2013 ACC/AHA Pooled Cohort coefficients
2. Write unit tests with known inputs → expected 10-year risk outputs
   (use the AHA online calculator as ground truth for test cases)
3. Wire into `integrate_survival()` as Layer 2
4. Add VO2 Max percentile lookup table and hazard modifier (Layer 3)
5. Add grip strength / dead hang hazard modifier (Layer 5)
6. Confirm: changing inputs produces expected directional changes in output

**Acceptance**: Unit tests pass. VO2 Max in top quartile meaningfully extends curve.

---

### Phase 3 — Apple Health parser
**Goal**: Auto-load biometrics from export.xml.

Tasks:
1. Implement `get_latest_biometrics(path) → dict` in `apple_health.py`
   using iterparse for memory efficiency
2. Map all Apple Health identifiers to model feature names
3. Handle missing fields gracefully (return None, never raise)
4. Write test with a small synthetic export.xml fixture
5. Print loaded values to console: `python -c "from src.apple_health import
   get_latest_biometrics; print(get_latest_biometrics('C:/Users/Daddy/Downloads/export.xml'))"`

**Acceptance**: Dict printed with correct values from real export.xml.

---

### Phase 4 — Basic GUI with Apple Health auto-fill
**Goal**: Window opens, loads Apple Health, shows input panels.

Tasks:
1. Implement main window in `gui.py` using CustomTkinter
2. Three-panel input layout matching mockup
3. On startup: call `get_latest_biometrics()` → populate read-only fields
   with green/amber indicators
4. Manual entry fields for smoking, diabetes, lab values, fitness tests
5. Calculate button calls model → prints result to console (chart not yet embedded)
6. Match colors and typography from `design/Longevity_Risk_Calculator_v2.html`

**Acceptance**: Window opens, Apple Health values visible, Calculate prints numbers.

---

### Phase 5 — Survival curve chart
**Goal**: Matplotlib chart embedded in results section.

Tasks:
1. Embed matplotlib figure in CustomTkinter window
2. Plot user curve (accent blue) vs population average (gray dashed)
3. X axis: current age to 100; Y axis: 0–100%
4. Three callout cards below chart: 5yr risk, 10yr risk, median years
5. Chart updates on each Calculate press (no flicker)

**Acceptance**: Chart renders, updates on recalculate, visually matches mockup.

---

### Phase 6 — Lab PDF drag-and-drop
**Goal**: Drop a blood panel PDF, fields auto-populate.

Tasks:
1. Implement `extract_lab_values(path) → dict` in `lab_parser.py`
   using Anthropic Vision API
2. Add `tkinterdnd2` drag-and-drop zone to Panel 2
3. On drop: show spinner, call `extract_lab_values()` in background thread,
   populate fields on completion
4. Handle API errors gracefully (show error message, leave fields blank)
5. Test with a real lab PDF

**Acceptance**: Drop a PDF, fields populate within 5 seconds.

---

### Phase 7 — Polish and edge cases
**Goal**: Production-ready for personal use.

Tasks:
1. Handle export.xml not found (show setup instructions)
2. Handle life table download failure (fallback message)
3. Handle missing Anthropic API key (disable PDF drop, show note)
4. Validate all numeric inputs with clear error messages
5. Window minimum size, resize behavior
6. Save last-used manual values to `~/.longevity_prefs.json`
   (smoking, diabetes, fitness test values — so they persist between sessions)
7. "Reload Apple Health" button to re-parse without restarting
8. Final visual QA against HTML mockup

**Acceptance**: App handles all error states gracefully, persists manual entries.

---

## Notes for Claude Code

- Build phases sequentially — do not skip ahead
- After each phase, confirm it runs before starting the next
- The HTML mockup in `design/` is the visual ground truth — reference it
  constantly during GUI work
- The Anthropic API key should be read from environment variable `ANTHROPIC_API_KEY`
- All file paths should use `pathlib.Path` for Windows compatibility
- Test on Windows (the user's platform) — watch for path separator issues
