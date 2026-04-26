# Longevity Risk Calculator

A personal longevity prediction app available in two forms:

| App | How to run | Status |
|-----|-----------|--------|
| **Web app** (JavaScript) | [GitHub Pages live URL](https://alanchudnow.github.io/Longevity/) | In progress — phases 1–2 complete |
| **Desktop app** (Python) | `python main.py` | Complete |

Both apps implement the same survival model. The web app runs entirely in the browser with no backend server.

---

## Web App

### Using it

Open the [live URL](https://alanchudnow.github.io/Longevity/) in any modern browser. No installation required.

### How it works

- **Apple Health** — drag and drop your `export.xml` from iPhone; the app streams it without loading the full file into memory
- **Lab results** — drag and drop a blood panel PDF (Labcorp or Sonora Quest); values populate automatically
- **Manual entry** — ZIP code, smoking, diabetes, grip strength / dead hang time
- **Calculate** — produces a survival curve, three callout cards, and a two-column risk factor panel

### Local development

```
python -m http.server 8765
# open http://localhost:8765
```

No build step. All dependencies are bundled in `lib/`.

### Architecture

| File | Responsibility |
|------|----------------|
| `js/health_models.js` | All survival math — life table, ASCVD, VO2, grip hazard |
| `js/apple_health.js` | Apple Health `export.xml` streaming parser |
| `js/lab_parser.js` | Lab PDF → cholesterol values via pdf.js |
| `js/zip_lookup.js` | ZIP code → life expectancy offset |
| `js/risk_factors.js` | Risk factor panel population |
| `js/chart.js` | Survival curve rendering (Chart.js) |
| `js/app.js` | Main wiring, tab routing, localStorage persistence |
| `index.html` | Entry point |
| `css/app.css` | All styles |

### Data files (pre-generated, no runtime downloads)

| File | Source | Size |
|------|--------|------|
| `data/life_table.json` | CDC NVSR 74-06 (2023), male + female | 12 KB |
| `data/zip_life_expectancy.json` | CDC USALEEP + Census ZCTA crosswalk | 4.3 MB |
| `data/vo2_norms.json` | Cooper Institute tables | 1 KB |

Run `python scripts/convert_data_to_json.py` to regenerate these from source (requires internet).

### Libraries (bundled locally, no CDN)

| Library | Version | Location |
|---------|---------|----------|
| pdf.js | 4.10.38 | `lib/pdfjs/` |
| Chart.js | 4.5.1 | `lib/chartjs/` |
| sax-js | latest | `lib/saxjs/` |

### Web app build phases

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Data files and bundled libraries | Complete |
| 2 | `health_models.js` — survival math | Complete |
| 3 | `lab_parser.js` — PDF extraction | Complete |
| 4 | `apple_health.js` — XML streaming | Complete |
| 5 | `index.html` and `css/app.css` — layout | Complete |
| 6 | `app.js` — full wiring | Pending |
| 7 | `chart.js` — survival curve | Pending |
| 8 | GitHub Pages deploy | Pending |

### Regression tests

Open test pages to verify logic in a browser:

```
http://localhost:8765/tests/test_health_models.html   # survival math
http://localhost:8765/tests/test_lab_parser.html      # PDF extraction (drop lab PDFs)
http://localhost:8765/tests/test_apple_health.html    # XML streaming (drop export.xml)
```

---

## Desktop App (Python)

Auto-fills biometrics from Apple Health, accepts blood panel PDFs, and produces a survival curve with risk callouts based on the ACC/AHA Pooled Cohort Equations and fitness modifiers.

### Features

- **Apple Health integration** — reads weight, height, BP, HR, HRV, VO2 max, and blood glucose from `export.xml`; supports last-value, 7-day, and 30-day averaging windows, plus a manual-entry mode
- **Lab results** — drop a blood panel PDF; extracts TC, HDL, LDL, ApoB, CRP, HbA1c, glucose automatically
- **Survival model** — CDC 2023 life table baseline × ASCVD Pooled Cohort Equations × VO2 max percentile × grip / dead hang modifier × geographic adjustment
- **Survival curve chart** — plots your trajectory vs. population baseline with 5-yr / 10-yr risk and median remaining years annotated
- **Risk factors panel** — two-column positive/negative factor breakdown sorted by impact
- **ZIP code adjustment** — CDC USALEEP geographic life expectancy baseline shift

### Requirements

- Python 3.10+
- Anaconda recommended

```
pip install -r requirements.txt
```

### Running

```
python main.py
```

Or double-click `run_app.bat` on Windows.

On first launch the app downloads the CDC life table and ZIP data (~5 MB, one-time).

### Testing

```
python -m pytest tests/
```

Or double-click `run_tests.bat`.

### Architecture

| File | Responsibility |
|------|----------------|
| `src/health_models.py` | All survival math — life table, ASCVD, VO2, grip, ZIP adjustment |
| `src/apple_health.py` | Apple Health `export.xml` parser |
| `src/lab_parser.py` | Lab PDF → cholesterol values via pdfplumber |
| `src/gui.py` | CustomTkinter UI — no business logic |
| `main.py` | Entry point — primes caches, launches GUI |

### Completed phases

| Phase | Description |
|-------|-------------|
| 1 | CDC 2023 life table download and parquet cache |
| 2 | ASCVD Pooled Cohort Equations + VO2 + grip hazard model |
| 3 | Apple Health export parser with windowed averaging |
| 4 | Three-panel CustomTkinter GUI |
| 5 | Survival curve matplotlib chart embedded in results |
| 6 | Historical date selector — scrub Apple Health data to any past month |
| 7 | BMI live auto-compute; optional field range validation; missing-input warning |
| 8 | CRP manual entry with FocusOut color feedback and infection warning |
| 9 | ZIP code geographic mortality adjustment — CDC USALEEP baseline shift |

### Roadmap

- Editable "what-if" scenario overlay on chart
