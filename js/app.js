/**
 * app.js — Phase 6 wiring
 * Connects all UI events to apple_health.js, lab_parser.js, health_models.js, zip_lookup.js.
 */

import { parseAppleHealth }   from './apple_health.js';
import { parseLabPdf }        from './lab_parser.js';
import {
  init as initModels,
  integrateSurvival, summarizeSurvival,
  predictCombinedHazard, ascvd10yrRisk, evaluateRiskFactors,
} from './health_models.js';
import { initZipLookup, lookupZip, getGeoHazard } from './zip_lookup.js';
import { renderSurvivalChart } from './chart.js';

// ── Constants ────────────────────────────────────────────────────────────────

const AH_FIELDS = [
  { key: 'age',          unit: 'yrs'       },
  { key: 'sex',          unit: ''          },
  { key: 'weight_lb',    unit: 'lb'        },
  { key: 'height_in',    unit: 'in'        },
  { key: 'bmi',          unit: ''          },
  { key: 'systolic_bp',  unit: 'mmHg'      },
  { key: 'resting_hr',   unit: 'bpm'       },
  { key: 'hrv',          unit: 'ms'        },
  { key: 'vo2_max',      unit: 'mL/min·kg' },
  { key: 'blood_glucose', unit: 'mg/dL'   },
];

const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

// localStorage key prefix
const LS = key => `lrc_${key}`;

// ── State ────────────────────────────────────────────────────────────────────

let _ahFile       = null;   // currently loaded Apple Health File object
let _ahData       = {};     // last parsed biometrics
let _ahRaw        = {};     // last parsed values as strings (for pre-filling manual mode)
let _zipInfo      = { found: false };
let _labExtras    = { hba1c: null, glucose: null };
let _modelsReady  = false;
let _zipReady     = false;

// ── DOM helpers ──────────────────────────────────────────────────────────────

const $  = id  => document.getElementById(id);
const el = sel => document.querySelector(sel);

// ── App init ─────────────────────────────────────────────────────────────────

async function appInit() {
  loadManualFields();
  initYearDropdown();
  wireAhPanel();
  wireLabPanel();
  wireManualPanel();
  wireCalculate();

  // Load models (needed for Calculate)
  try {
    await initModels('data');
    _modelsReady = true;
  } catch (e) {
    console.error('Models failed to load:', e);
  }

  // Load ZIP data in background — non-blocking
  initZipLookup('data')
    .then(() => {
      _zipReady = true;
      // Re-run ZIP lookup if the field already has a value (restored from localStorage)
      const z = $('inp-zip').value.trim();
      if (z) doZipLookup(z);
    })
    .catch(e => console.warn('ZIP data unavailable:', e));
}

// ── Year dropdown init ───────────────────────────────────────────────────────

function initYearDropdown() {
  const now = new Date().getFullYear();
  populateYearDropdown(now - 9, now, now - 1);
}

function populateYearDropdown(minYear, maxYear, selectedYear) {
  const sel = $('ah-year');
  sel.innerHTML = '';
  for (let y = maxYear; y >= minYear; y--) {
    const opt = document.createElement('option');
    opt.value = y;
    opt.textContent = y;
    if (y === selectedYear) opt.selected = true;
    sel.appendChild(opt);
  }
}

// ── Apple Health panel ───────────────────────────────────────────────────────

function wireAhPanel() {
  // Browse / Reload button
  $('ah-reload-btn').addEventListener('click', () => $('file-ah').click());

  // File input → parse
  $('file-ah').addEventListener('change', e => {
    if (e.target.files[0]) handleAhFile(e.target.files[0]);
  });

  // Drop zone drag-and-drop (click already handled by HTML onclick)
  const zone = $('drop-ah');
  zone.addEventListener('dragover',  e => { e.preventDefault(); zone.classList.add('dragover'); });
  zone.addEventListener('dragleave', ()  => zone.classList.remove('dragover'));
  zone.addEventListener('drop', e => {
    e.preventDefault();
    zone.classList.remove('dragover');
    const file = e.dataTransfer.files[0];
    if (file) handleAhFile(file);
  });

  // Window selector
  $('ah-window').addEventListener('change', onWindowChange);

  // Current / Historical radio buttons
  document.querySelectorAll('input[name=ah-mode]').forEach(r =>
    r.addEventListener('change', onDateModeChange)
  );

  // Month / year selectors in historical mode
  $('ah-month').addEventListener('change', reparseAh);
  $('ah-year').addEventListener('change',  reparseAh);

  // BMI auto-compute in manual mode
  $('inp-weight_lb').addEventListener('input', updateBmi);
  $('inp-height_in').addEventListener('input', updateBmi);
}

async function handleAhFile(file) {
  _ahFile = file;

  const zone     = $('drop-ah');
  const statusEl = $('ah-status');
  const windowMode = $('ah-window').value;

  zone.classList.remove('loaded', 'error', 'dragover');
  statusEl.textContent  = 'Parsing…';
  statusEl.className    = 'panel-status status-dim';

  // In manual mode, just register the file without parsing
  if (windowMode === 'none') {
    zone.classList.add('loaded');
    zone.innerHTML = `<strong>${file.name}</strong><div class="drop-zone-hint">File registered (manual entry mode)</div>`;
    $('ah-reload-btn').textContent = '↺ Change file';
    return;
  }

  zone.textContent = `Parsing… 0%`;

  const asOf = getAsOf();
  try {
    const data = await parseAppleHealth(file, windowMode, asOf, frac => {
      zone.textContent = `Parsing… ${Math.round(frac * 100)}%`;
    });

    _ahData = data;
    populateAhFields(data);

    // Update year dropdown from actual export date range
    const [minDt, maxDt] = data._date_range;
    if (minDt !== null && maxDt !== null) {
      const minY = new Date(minDt).getUTCFullYear();
      const maxY = new Date(maxDt).getUTCFullYear();
      const curY = parseInt($('ah-year').value) || maxY - 1;
      populateYearDropdown(minY, maxY, Math.min(Math.max(curY, minY), maxY));
    }

    zone.classList.add('loaded');
    zone.innerHTML = `<strong>${file.name}</strong><div class="drop-zone-hint">${(file.size / 1e6).toFixed(0)} MB · streamed</div>`;
    $('ah-reload-btn').textContent = '↺ Reload Apple Health file';

  } catch (err) {
    zone.classList.add('error');
    zone.textContent = `Error: ${err.message}`;
    statusEl.textContent = 'Parse error';
    statusEl.className   = 'panel-status status-err';
    console.error(err);
  }
}

function populateAhFields(data) {
  const counts = data._counts || {};
  let found = 0;

  for (const { key, unit } of AH_FIELDS) {
    const val   = data[key];
    const dotEl = $(`dot-${key}`);
    const valEl = $(`val-${key}`);

    if (val !== null && val !== undefined) {
      found++;
      dotEl.className   = 'dot ok';
      const n = counts[key];
      const suffix = n && n > 1 ? ` (avg ${n})` : '';
      valEl.textContent = unit ? `${val} ${unit}${suffix}` : `${val}${suffix}`;
      valEl.className   = 'ah-field-value populated';
      _ahRaw[key] = String(val);
    } else {
      dotEl.className   = 'dot warn';
      valEl.textContent = '—';
      valEl.className   = 'ah-field-value';
      delete _ahRaw[key];
    }
  }

  const statusEl  = $('ah-status');
  const total     = AH_FIELDS.length;
  const isHist    = el('input[name=ah-mode]:checked').value === 'historical';

  if (isHist) {
    const m = $('ah-month').options[$('ah-month').selectedIndex].text;
    const y = $('ah-year').value;
    statusEl.textContent = found === 0
      ? `No data before ${m} ${y}`
      : `as of ${m} ${y}  •  ${found}/${total} fields`;
    statusEl.className = `panel-status ${found === 0 ? 'status-warn' : 'status-ok'}`;
  } else {
    statusEl.textContent = `Connected  •  ${found}/${total} fields`;
    statusEl.className   = `panel-status ${found > 0 ? 'status-ok' : 'status-warn'}`;
  }

  $('ah-timestamp').textContent = `Loaded ${new Date().toLocaleTimeString()}`;
}

function onWindowChange() {
  const mode = $('ah-window').value;
  if (mode === 'none') {
    setAhManualMode(true);
  } else {
    setAhManualMode(false);
    reparseAh();
  }
}

function setAhManualMode(on) {
  const panel = $('panel-ah');
  if (on) {
    panel.classList.add('ah-mode-manual');
    // Pre-fill inputs with last-known AH values
    for (const { key } of AH_FIELDS) {
      const inp = $(`inp-${key}`);
      if (inp && !inp.readOnly && _ahRaw[key] !== undefined) {
        inp.value = _ahRaw[key];
      }
    }
    updateBmi();
    $('ah-status').textContent = 'Manual entry';
    $('ah-status').className   = 'panel-status status-dim';
  } else {
    panel.classList.remove('ah-mode-manual');
  }
}

function onDateModeChange() {
  const isHist = el('input[name=ah-mode]:checked').value === 'historical';
  $('hist-selectors').classList.toggle('visible', isHist);
  if ($('ah-window').value !== 'none') reparseAh();
}

function reparseAh() {
  if (_ahFile && $('ah-window').value !== 'none') handleAhFile(_ahFile);
}

function getAsOf() {
  if (el('input[name=ah-mode]:checked').value !== 'historical') return null;
  const monthStr = $('ah-month').options[$('ah-month').selectedIndex].text;
  const monthIdx = MONTHS.indexOf(monthStr);
  const year     = parseInt($('ah-year').value);
  if (monthIdx < 0 || isNaN(year)) return null;
  const lastDay  = new Date(year, monthIdx + 1, 0).getDate();
  return new Date(Date.UTC(year, monthIdx, lastDay, 23, 59, 59));
}

function updateBmi() {
  const w = parseFloat($('inp-weight_lb').value);
  const h = parseFloat($('inp-height_in').value);
  if (!isNaN(w) && !isNaN(h) && h > 0) {
    const bmi = Math.round((w / h ** 2) * 703.0 * 10) / 10;
    $('inp-bmi').value     = bmi;
    $('dot-bmi').className = 'dot ok';
  } else {
    $('inp-bmi').value     = '';
    $('dot-bmi').className = 'dot warn';
  }
}

// ── Lab panel ────────────────────────────────────────────────────────────────

function wireLabPanel() {
  const zone  = $('drop-lab');
  const input = $('file-lab');

  zone.addEventListener('dragover',  e => { e.preventDefault(); zone.classList.add('dragover'); });
  zone.addEventListener('dragleave', ()  => zone.classList.remove('dragover'));
  zone.addEventListener('drop', e => {
    e.preventDefault();
    zone.classList.remove('dragover');
    const file = e.dataTransfer.files[0];
    if (file) handleLabFile(file);
  });

  // File input change — handles both drop-zone click and Browse button
  input.addEventListener('change', e => {
    if (e.target.files[0]) handleLabFile(e.target.files[0]);
  });

  // CRP blur color feedback
  $('lab-crp').addEventListener('blur', onCrpBlur);
}

async function handleLabFile(file) {
  const zone     = $('drop-lab');
  const statusEl = $('lab-status');

  zone.classList.remove('loaded', 'error', 'dragover');
  zone.textContent       = `Parsing ${file.name}…`;
  statusEl.textContent   = 'Reading PDF…';
  statusEl.className     = 'lab-status status-dim';

  try {
    const data = await parseLabPdf(file);
    _labExtras = { hba1c: data.hba1c ?? null, glucose: data.glucose ?? null };

    // Populate visible lab fields
    const FIELD_MAP = {
      total_cholesterol: 'lab-total_cholesterol',
      hdl:               'lab-hdl',
      ldl:               'lab-ldl',
      triglycerides:     'lab-triglycerides',
      apob:              'lab-apob',
      crp:               'lab-crp',
    };
    const SILENT = new Set(['crp']);

    let populated = 0;
    const missing = [];

    for (const [srcKey, elId] of Object.entries(FIELD_MAP)) {
      const val = data[srcKey];
      if (val !== null && val !== undefined) {
        $(elId).value = String(Math.round(val * 10) / 10);
        populated++;
      } else if (!SILENT.has(srcKey)) {
        missing.push(srcKey);
      }
    }

    const SHORT = { total_cholesterol:'TC', hdl:'HDL', ldl:'LDL',
                    triglycerides:'Trig', apob:'ApoB' };
    const vendor   = (data.lab_vendor || 'unknown')
                       .replace(/_/g, ' ')
                       .replace(/\b\w/g, c => c.toUpperCase());
    const datePart = data.lab_date ? ` — ${data.lab_date}` : '';
    const extracted = ['total_cholesterol','hdl','ldl','triglycerides','apob',
                       'hba1c','glucose','crp','vldl','non_hdl']
                        .filter(k => data[k] !== null && data[k] !== undefined).length;

    let status = `${vendor}${datePart} — ${extracted} values extracted`;
    if (missing.length) {
      status += ` (${missing.map(k => SHORT[k] || k).join(', ')} not on panel)`;
    }

    statusEl.textContent = status;
    statusEl.className   = `lab-status ${populated > 0 ? 'status-ok' : 'status-warn'}`;

    zone.classList.add('loaded');
    zone.innerHTML = `<strong>${file.name}</strong><div class="drop-zone-hint">${vendor}${datePart}</div>`;

    if (data.crp !== null && data.crp !== undefined) onCrpBlur();
    saveManualFields();

  } catch (err) {
    zone.classList.add('error');
    zone.textContent   = `Error: ${err.message}`;
    statusEl.textContent = 'Could not read PDF — please enter values manually';
    statusEl.className   = 'lab-status status-warn';
    console.error(err);
  }
}

function parseCrp(s) {
  s = (s || '').trim();
  if (!s) return null;
  if (s.startsWith('<')) {
    const v = parseFloat(s.slice(1));
    return isNaN(v) ? null : v;
  }
  const v = parseFloat(s);
  return isNaN(v) || v < 0 || v > 100 ? null : v;
}

function onCrpBlur() {
  const raw    = $('lab-crp').value.trim();
  const inp    = $('lab-crp');
  const warnEl = $('crp-infection-warn');
  warnEl.style.display = 'none';

  if (!raw) { inp.style.color = ''; return; }
  const crp = parseCrp(raw);
  if (crp === null) { inp.style.color = 'var(--danger)'; return; }

  if      (crp <= 1.0) inp.style.color = 'var(--ok)';
  else if (crp <= 3.0) inp.style.color = 'var(--ink-1)';
  else if (crp <= 10.0) inp.style.color = 'var(--warn)';
  else {
    inp.style.color          = 'var(--danger)';
    warnEl.style.display     = 'block';
  }
}

// ── Manual panel ─────────────────────────────────────────────────────────────

function wireManualPanel() {
  const zipInp = $('inp-zip');
  zipInp.addEventListener('blur',    () => doZipLookup(zipInp.value));
  zipInp.addEventListener('keydown', e  => { if (e.key === 'Enter') doZipLookup(zipInp.value); });

  // Persist all manual fields on change
  [
    'inp-smoker', 'inp-diabetes', 'inp-zip',
    'inp-grip_kg', 'inp-hang_seconds',
    'lab-total_cholesterol', 'lab-hdl', 'lab-ldl',
    'lab-apob', 'lab-triglycerides', 'lab-crp',
  ].forEach(id => {
    const e = $(id);
    if (e) e.addEventListener('change', saveManualFields);
  });
}

async function doZipLookup(zip) {
  const statusEl = $('zip-status');
  zip = (zip || '').trim();

  if (!zip) { statusEl.textContent = ''; _zipInfo = { found: false }; return; }

  if (!/^\d{5}$/.test(zip)) {
    statusEl.textContent = 'Enter a 5-digit ZIP';
    statusEl.className   = 'zip-status status-warn';
    _zipInfo = { found: false };
    return;
  }

  if (!_zipReady) {
    statusEl.textContent = 'Loading geographic data…';
    statusEl.className   = 'zip-status status-dim';
    try {
      await initZipLookup('data');
      _zipReady = true;
    } catch {
      statusEl.textContent = 'Geographic data unavailable — national baseline used';
      statusEl.className   = 'zip-status status-dim';
      return;
    }
  }

  _zipInfo = lookupZip(zip);

  if (!_zipInfo.found) {
    statusEl.textContent = 'ZIP not in geographic dataset — national baseline used';
    statusEl.className   = 'zip-status status-dim';
    return;
  }

  const { county_name, state_abbr, life_expectancy, offset } = _zipInfo;
  const loc  = county_name && state_abbr ? `${county_name}, ${state_abbr}` : zip;
  const sign = offset >= 0 ? '+' : '';
  statusEl.textContent = `${loc}  •  LE ${life_expectancy.toFixed(1)} yrs  (${sign}${offset.toFixed(1)} vs national avg)`;
  statusEl.className   = `zip-status ${offset >= 0 ? 'status-ok' : 'status-warn'}`;
}

// ── localStorage ──────────────────────────────────────────────────────────────

const LS_MAP = {
  smoker:            'inp-smoker',
  diabetes:          'inp-diabetes',
  zip:               'inp-zip',
  grip_kg:           'inp-grip_kg',
  hang_seconds:      'inp-hang_seconds',
  total_cholesterol: 'lab-total_cholesterol',
  hdl:               'lab-hdl',
  ldl:               'lab-ldl',
  apob:              'lab-apob',
  triglycerides:     'lab-triglycerides',
  crp:               'lab-crp',
};

function saveManualFields() {
  for (const [key, elId] of Object.entries(LS_MAP)) {
    const e = $(elId);
    if (!e) continue;
    localStorage.setItem(LS(key), e.type === 'checkbox' ? (e.checked ? '1' : '0') : e.value);
  }
}

function loadManualFields() {
  for (const [key, elId] of Object.entries(LS_MAP)) {
    const e    = $(elId);
    const saved = localStorage.getItem(LS(key));
    if (!e || saved === null) continue;
    if (e.type === 'checkbox') e.checked = saved === '1';
    else e.value = saved;
  }
}

// ── Calculate ─────────────────────────────────────────────────────────────────

function wireCalculate() {
  $('btn-calculate').addEventListener('click', onCalculate);
}

function pf(s) {
  const v = parseFloat((s || '').trim());
  return isNaN(v) ? null : v;
}

function onCalculate() {
  if (!_modelsReady) {
    $('missing-warn').textContent = '⚠  Models still loading — please wait a moment.';
    $('missing-warn').style.display = 'block';
    return;
  }

  const isManual = $('ah-window').value === 'none';

  // ── AH-derived values ─────────────────────────────────────────────────────
  let age, sex, sbp, vo2, weightKg, bmi, restingHr, hrv, bloodGlucose;

  if (isManual) {
    const ageStr = $('inp-age').value.trim();
    age  = ageStr ? Math.round(parseFloat(ageStr)) : null;
    const s = $('inp-sex').value.trim().toLowerCase();
    sex  = (s === 'male' || s === 'female') ? s : 'male';
    sbp  = pf($('inp-systolic_bp').value);
    if (sbp !== null && (sbp < 70 || sbp > 260)) sbp = null;
    vo2  = pf($('inp-vo2_max').value);
    if (vo2 !== null && (vo2 < 10 || vo2 > 90)) vo2 = null;
    const wlb = pf($('inp-weight_lb').value);
    weightKg   = wlb !== null ? wlb * 0.453592 : null;
    bmi        = pf($('inp-bmi').value);
    restingHr  = pf($('inp-resting_hr').value);
    hrv        = pf($('inp-hrv').value);
    bloodGlucose = pf($('inp-blood_glucose').value);
  } else {
    age          = _ahData.age          ?? null;
    sex          = _ahData.sex          || 'male';
    sbp          = _ahData.systolic_bp  ?? null;
    vo2          = _ahData.vo2_max      ?? null;
    weightKg     = _ahData.weight_kg    ?? null;
    bmi          = _ahData.bmi          ?? null;
    restingHr    = _ahData.resting_hr   ?? null;
    hrv          = _ahData.hrv          ?? null;
    bloodGlucose = _ahData.blood_glucose ?? null;
  }

  if (age === null || isNaN(age)) {
    $('missing-warn').textContent = '⚠  Age is required — drop export.xml or switch to Manual entry mode';
    $('missing-warn').style.display = 'block';
    return;
  }

  // ── Lab values ────────────────────────────────────────────────────────────
  const tc   = pf($('lab-total_cholesterol').value);
  const hdl  = pf($('lab-hdl').value);
  const ldl  = pf($('lab-ldl').value);
  const trig = pf($('lab-triglycerides').value);
  const apob = pf($('lab-apob').value);
  const crpRaw   = parseCrp($('lab-crp').value);
  const crpModel = crpRaw !== null && crpRaw <= 1.0 ? 0.5 : crpRaw;

  // ── Manual panel values ───────────────────────────────────────────────────
  const smoker   = $('inp-smoker').value;
  const diabetes = $('inp-diabetes').checked;
  const gripKg   = pf($('inp-grip_kg').value);
  const hangSec  = pf($('inp-hang_seconds').value);
  const zip      = $('inp-zip').value.trim();

  // ── Missing high-impact warning ───────────────────────────────────────────
  const missingHigh = [];
  if (vo2  === null) missingHigh.push('VO2 Max');
  if (gripKg === null && hangSec === null) missingHigh.push('Grip Strength');
  const warnEl = $('missing-warn');
  if (missingHigh.length) {
    warnEl.textContent    = `⚠  ${missingHigh.join(' and ')} not entered — fitness layers using neutral estimate`;
    warnEl.style.display  = 'block';
  } else {
    warnEl.style.display  = 'none';
  }

  // ── Build features object ─────────────────────────────────────────────────
  const features = {
    age, sex, race: 'white',
    total_cholesterol: tc, hdl, ldl, triglycerides: trig, apob,
    systolic_bp:  sbp,
    smoker, diabetes, bp_treated: false,
    vo2_max:      vo2,
    grip_kg:      gripKg,
    hang_seconds: hangSec,
    weight_kg:    weightKg,
    bmi, resting_hr: restingHr, hrv, blood_glucose: bloodGlucose,
    crp:          crpModel,
    crp_raw:      crpRaw,
    hba1c:        _labExtras.hba1c,
  };

  // ── Geo adjustment ────────────────────────────────────────────────────────
  const geo = (_zipInfo.found && zip) ? getGeoHazard(zip) : 1.0;

  // ── Survival model ────────────────────────────────────────────────────────
  const relHazard     = predictCombinedHazard(features) * geo;
  const userCurve     = integrateSurvival(age, sex, relHazard);
  const baselineCurve = integrateSurvival(age, sex, geo);   // population = geo only
  const { risk5, risk10, medianYears } = summarizeSurvival(userCurve, age);

  const ascvd10 = (tc !== null && hdl !== null) ? ascvd10yrRisk(features) : null;

  // ── Update stat cards ─────────────────────────────────────────────────────
  $('stat-r5').textContent    = `${(risk5   * 100).toFixed(1)}%`;
  $('stat-r10').textContent   = `${(risk10  * 100).toFixed(1)}%`;
  $('stat-med').textContent   = `${Math.round(medianYears)} yrs`;
  $('stat-ascvd').textContent = ascvd10 !== null ? `${(ascvd10 * 100).toFixed(1)}%` : '—';

  // ── Data source label ─────────────────────────────────────────────────────
  let sourceText = 'Based on ';
  if (isManual) {
    sourceText += 'manually entered data';
  } else if (el('input[name=ah-mode]:checked').value === 'historical') {
    const m = $('ah-month').options[$('ah-month').selectedIndex].text;
    sourceText += `Apple Health data as of ${m} ${$('ah-year').value}`;
  } else {
    sourceText += 'current Apple Health data';
  }
  if (_zipInfo.found && zip) {
    const { county_name, state_abbr, offset } = _zipInfo;
    const sign = offset >= 0 ? '+' : '';
    sourceText += `  •  ZIP ${zip} (${county_name}, ${state_abbr}, LE ${sign}${offset.toFixed(1)} vs national)`;
  }
  $('data-source-label').textContent = sourceText;

  // ── Risk factors panel ────────────────────────────────────────────────────
  const { positive, negative } = evaluateRiskFactors(features);
  updateRiskPanel(positive, negative);

  // ── Chart labels ──────────────────────────────────────────────────────────
  let userLabel = 'You';
  if (isManual) {
    userLabel = 'You (manual)';
  } else if (el('input[name=ah-mode]:checked').value === 'historical') {
    const m = $('ah-month').options[$('ah-month').selectedIndex].text;
    userLabel = `You (${m} ${$('ah-year').value})`;
  }
  let baselineLabel = 'Population average';
  if (_zipInfo.found && zip) {
    const { county_name, state_abbr } = _zipInfo;
    baselineLabel = county_name && state_abbr
      ? `${county_name}, ${state_abbr} avg`
      : 'Local average';
  }

  // ── Survival chart ────────────────────────────────────────────────────────
  renderSurvivalChart('survival-chart', {
    userCurve, baselineCurve, age, relHazard, geo,
    risk5, risk10, medianYears,
    userLabel, baselineLabel,
  });

  // Switch to Results tab
  window.switchTab('results');
  saveManualFields();
}

// ── Risk factors panel ────────────────────────────────────────────────────────

function updateRiskPanel(positive, negative) {
  const ORDER = { high: 0, medium: 1, low: 2 };

  const pos = positive.slice().sort((a, b) => (ORDER[a.impact] ?? 3) - (ORDER[b.impact] ?? 3));
  const neg = negative.slice().sort((a, b) => (ORDER[a.impact] ?? 3) - (ORDER[b.impact] ?? 3));

  function render(items, container, isPositive) {
    container.innerHTML = '';
    if (items.length === 0) {
      container.innerHTML = isPositive
        ? '<div class="risk-empty">Enter optional fields for full analysis</div>'
        : '<div class="risk-empty">No significant risk factors identified</div>';
      return;
    }
    items.forEach(({ label }) => {
      const isMissing = label.toLowerCase().includes('not entered');
      const d = document.createElement('div');
      d.className   = `risk-item ${isPositive ? 'positive' : isMissing ? 'missing' : 'negative'}`;
      d.textContent = `${isPositive ? '✅' : '❌'}  ${label}`;
      container.appendChild(d);
    });
  }

  render(pos, $('risk-positive'), true);
  render(neg, $('risk-negative'), false);
  $('risk-panel').classList.add('visible');
}

// ── Start ─────────────────────────────────────────────────────────────────────

appInit().catch(console.error);
