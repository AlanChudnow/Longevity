# Longevity Risk Calculator

A personal longevity prediction desktop app built with Python and CustomTkinter.

Auto-fills biometrics from Apple Health, accepts blood panel PDFs, and produces a survival curve with risk callouts based on the ACC/AHA Pooled Cohort Equations and fitness modifiers.

## Features

- **Apple Health integration** — reads weight, height, BP, HR, HRV, VO2 max, and blood glucose from `export.xml`; supports last-value, 7-day, and 30-day averaging windows, plus a manual-entry mode
- **Lab results** — drop a blood panel PDF onto the app; Claude extracts TC, HDL, LDL, and ApoB automatically
- **Survival model** — CDC 2023 life table baseline × ASCVD Pooled Cohort Equations × VO2 max percentile × grip / dead hang modifier
- **Survival curve chart** — plots your trajectory vs. population baseline with 5-yr / 10-yr risk and median remaining years annotated

## Requirements

- Python 3.10+
- Anaconda recommended (see `requirements.txt`)
- `ANTHROPIC_API_KEY` environment variable set (for lab PDF extraction)

```
pip install -r requirements.txt
```

## Running

```
python main.py
```

Or double-click `run_app.bat` on Windows.

On first launch the app downloads the CDC life table (~2 MB, one-time).

## Testing

```
python -m pytest tests/
```

Or double-click `run_tests.bat`.

## Architecture

| File | Responsibility |
|------|----------------|
| `src/health_models.py` | All survival math — life table, ASCVD, VO2, grip hazard |
| `src/apple_health.py` | Apple Health `export.xml` parser |
| `src/lab_parser.py` | Lab PDF → cholesterol values via Claude API |
| `src/gui.py` | CustomTkinter UI — no business logic |
| `main.py` | Entry point — primes life table cache, launches GUI |

## Completed phases

| Phase | Description |
|-------|-------------|
| 1 | CDC 2023 life table download and parquet cache |
| 2 | ASCVD Pooled Cohort Equations + VO2 + grip hazard model |
| 3 | Apple Health export parser with windowed averaging |
| 4 | Three-panel CustomTkinter GUI |
| 5 | Survival curve matplotlib chart embedded in results |
| 6 | Historical date selector — scrub Apple Health data to any past month |
| 7 | BMI live auto-compute; optional field range validation; missing-input warning |

## Roadmap

- HRV + resting HR autonomic hazard modifier (Layer 4)
- Editable "what-if" scenario overlay on chart
