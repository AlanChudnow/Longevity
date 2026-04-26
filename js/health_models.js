/**
 * health_models.js — survival modeling, JS translation of src/health_models.py
 *
 * Layer 1: CDC life table baseline (mx)
 * Layer 2: ACC/AHA Pooled Cohort Equations (ASCVD)
 * Layer 3: VO2 Max percentile (Cooper Institute norms)
 * Layer 5: Grip strength / dead hang (Lancet PURE coefficients)
 *
 * Call init(dataPath) once before using any other export.
 * All functions after init are synchronous.
 */

// ─────────────────────────────────────────────────────────────────────────────
// Module state
// ─────────────────────────────────────────────────────────────────────────────

let _lifeTableMap = null;  // Map("male:0") -> mx, for ages 0–(maxTableAge)
let _maxTableAge  = 99;    // last age before the CDC closing row (qx=1)
let _gompertz     = {};    // { male: {a, b}, female: {a, b} } — Gompertz tail params
let _vo2Norms     = null;  // { norms: {male:[...], female:[...]}, hazard_ratios:{...} }

// ─────────────────────────────────────────────────────────────────────────────
// Initialisation — call once before anything else
// ─────────────────────────────────────────────────────────────────────────────

export async function init(dataPath = 'data') {
  const [lifeTableRows, vo2Data] = await Promise.all([
    fetch(`${dataPath}/life_table.json`).then(r => r.json()),
    fetch(`${dataPath}/vo2_norms.json`).then(r => r.json()),
  ]);
  _vo2Norms = vo2Data;
  _buildLifeTable(lifeTableRows);
  _fitGompertz(lifeTableRows);
}

// ─────────────────────────────────────────────────────────────────────────────
// Life table internals
// ─────────────────────────────────────────────────────────────────────────────

function _buildLifeTable(rows) {
  const maxYear = Math.max(...rows.map(r => r.year));
  _lifeTableMap = new Map();
  let maxAge = 0;
  for (const r of rows) {
    if (r.year !== maxYear) continue;
    // Exclude the CDC closing row: mx >= ln(1/1e-10) ≈ 23 means qx was 1.0
    if (r.mx < 20) {
      _lifeTableMap.set(`${r.sex}:${r.age}`, r.mx);
      if (r.age > maxAge) maxAge = r.age;
    }
  }
  _maxTableAge = maxAge;
}

function _fitGompertz(rows) {
  // Fit ln(mx) = a + b*age on the last 15 ages of the table.
  // Simple OLS: b = (n·ΣXY - ΣX·ΣY) / (n·ΣX² - (ΣX)²), a = (ΣY - b·ΣX)/n
  const maxYear = Math.max(...rows.map(r => r.year));
  for (const sex of ['male', 'female']) {
    const fitRows = rows
      .filter(r => r.year === maxYear && r.sex === sex && r.mx < 20 &&
                   r.age >= _maxTableAge - 14 && r.age <= _maxTableAge)
      .sort((a, b) => a.age - b.age);
    const n = fitRows.length;
    let sumX = 0, sumY = 0, sumXY = 0, sumX2 = 0;
    for (const r of fitRows) {
      const y = Math.log(r.mx);
      sumX  += r.age;
      sumY  += y;
      sumXY += r.age * y;
      sumX2 += r.age * r.age;
    }
    const b = (n * sumXY - sumX * sumY) / (n * sumX2 - sumX * sumX);
    const a = (sumY - b * sumX) / n;
    _gompertz[sex] = { a, b };
  }
}

/**
 * Return the baseline annual mortality rate (mx) for a given integer age and sex.
 * Uses CDC life table for ages 0–99; Gompertz extrapolation for 100+.
 */
function _lifeTableMx(age, sex) {
  age = Math.round(age);
  sex = (sex || 'male').toLowerCase();
  if (age <= _maxTableAge) {
    const key = `${sex}:${age}`;
    if (_lifeTableMap.has(key)) return _lifeTableMap.get(key);
    // Nearest available age fallback
    for (let d = 1; d <= 10; d++) {
      if (_lifeTableMap.has(`${sex}:${age - d}`)) return _lifeTableMap.get(`${sex}:${age - d}`);
      if (_lifeTableMap.has(`${sex}:${age + d}`)) return _lifeTableMap.get(`${sex}:${age + d}`);
    }
  }
  // Gompertz extrapolation: ln(mx) = a + b*age
  const { a, b } = _gompertz[sex] || _gompertz['male'];
  return Math.exp(a + b * age);
}

// ─────────────────────────────────────────────────────────────────────────────
// Layer 1 — Survival integration
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Integrate the survival function from age0 with piecewise-constant hazards.
 * relHazard multiplicatively scales baseline mx at every age step.
 * Returns array of { age, S, h } (S = cumulative survival probability).
 */
export function integrateSurvival(age0, sex, relHazard = 1.0, ageMax = 110) {
  sex = (sex || 'male').toLowerCase();
  age0 = Math.round(age0);
  const rh = Math.max(0, relHazard);
  let S = 1.0;
  const curve = [];
  for (let age = age0; age <= ageMax; age++) {
    const mx = _lifeTableMx(age, sex);
    const h = mx * rh;
    S *= Math.exp(-h);
    curve.push({ age, S: Math.max(0, S), h });
    if (S < 1e-6) break;
  }
  return curve;
}

/**
 * Compute 5-year risk, 10-year risk, and median remaining years from a survival curve.
 * Returns { risk5, risk10, medianYears }.
 */
export function summarizeSurvival(curve, age0) {
  if (!curve.length) return { risk5: 0, risk10: 0, medianYears: 0 };

  const byAge = new Map(curve.map(r => [r.age, r.S]));
  const maxAge = Math.max(...byAge.keys());

  function sAt(delta) {
    return byAge.get(Math.round(age0) + delta) ?? byAge.get(maxAge) ?? 0;
  }

  const risk5  = Math.max(0, Math.min(1, 1 - sAt(5)));
  const risk10 = Math.max(0, Math.min(1, 1 - sAt(10)));

  // Median: interpolate within year S first crosses 0.5
  let medianYears = null;
  let prevAge = Math.round(age0), prevS = 1.0;
  for (const { age, S } of curve) {
    if (S <= 0.5) {
      const t0 = prevAge - age0, t1 = age - age0;
      const frac = prevS !== S ? (0.5 - prevS) / (S - prevS) : 0;
      medianYears = t0 + Math.max(0, Math.min(1, frac)) * (t1 - t0);
      break;
    }
    prevAge = age;
    prevS   = S;
  }

  if (medianYears === null) {
    // Survival never drops to 50% — use area under curve (≈ mean LE)
    let A = 0;
    let pAge = age0, pS = 1.0;
    for (const { age, S } of curve) {
      A += 0.5 * (pS + S) * (age - pAge);
      pAge = age; pS = S;
    }
    medianYears = A;
  }

  return { risk5, risk10, medianYears };
}

// ─────────────────────────────────────────────────────────────────────────────
// Layer 2 — ACC/AHA 2013 Pooled Cohort Equations
// Reference: Goff et al., JACC 2014 (doi:10.1016/j.jacc.2013.11.005)
// ─────────────────────────────────────────────────────────────────────────────

const _PCE = {
  white_male: {
    coef: {
      ln_age:            12.344,
      ln_tc:             11.853,
      ln_age_ln_tc:      -2.664,
      ln_hdl:            -7.990,
      ln_age_ln_hdl:      1.769,
      ln_sbp_treated:     1.797,
      ln_sbp_untreated:   1.764,
      smoker:             7.837,
      ln_age_smoker:     -1.795,
      diabetes:           0.661,
    },
    meanCoef:      61.18,
    baselineS10:    0.9144,
  },
  aa_male: {
    coef: {
      ln_age:            2.469,
      ln_tc:             0.302,
      ln_hdl:           -0.307,
      ln_sbp_treated:    1.916,
      ln_sbp_untreated:  1.809,
      smoker:            0.549,
      diabetes:          0.645,
    },
    meanCoef:      19.54,
    baselineS10:    0.8954,
  },
  white_female: {
    coef: {
      ln_age:            -29.799,
      ln_age_sq:           4.884,
      ln_tc:              13.540,
      ln_age_ln_tc:       -3.114,
      ln_hdl:            -13.578,
      ln_age_ln_hdl:       3.149,
      ln_sbp_treated:      2.019,
      ln_sbp_untreated:    1.957,
      smoker:              7.574,
      ln_age_smoker:      -1.665,
      diabetes:            0.661,
    },
    meanCoef:     -29.799,
    baselineS10:    0.9665,
  },
  aa_female: {
    coef: {
      ln_age:             17.1141,
      ln_tc:               0.9396,
      ln_hdl:            -18.9196,
      ln_age_ln_hdl:       4.4748,
      ln_sbp_treated:     29.2907,
      ln_age_ln_sbp_t:    -6.4321,
      ln_sbp_untreated:   27.8197,
      ln_age_ln_sbp_u:    -6.0873,
      smoker:              0.8738,
      diabetes:            0.8738,
    },
    meanCoef:      86.6081,
    baselineS10:    0.9533,
  },
};

function _pceSum(age, tc, hdl, sbp, smoker, diabetes, bpTreated, stratum) {
  const c  = _PCE[stratum].coef;
  const la = Math.log(age), ltc = Math.log(tc), lhdl = Math.log(hdl), lsbp = Math.log(sbp);
  const sm = smoker ? 1 : 0, dm = diabetes ? 1 : 0;

  let s = 0;
  s += (c.ln_age         || 0) * la;
  s += (c.ln_age_sq      || 0) * la * la;
  s += (c.ln_tc          || 0) * ltc;
  s += (c.ln_age_ln_tc   || 0) * la * ltc;
  s += (c.ln_hdl         || 0) * lhdl;
  s += (c.ln_age_ln_hdl  || 0) * la * lhdl;
  if (bpTreated) {
    s += (c.ln_sbp_treated    || 0) * lsbp;
    s += (c.ln_age_ln_sbp_t   || 0) * la * lsbp;
  } else {
    s += (c.ln_sbp_untreated  || 0) * lsbp;
    s += (c.ln_age_ln_sbp_u   || 0) * la * lsbp;
  }
  s += (c.smoker         || 0) * sm;
  s += (c.ln_age_smoker  || 0) * la * sm;
  s += (c.diabetes       || 0) * dm;
  return s;
}

/**
 * ACC/AHA Pooled Cohort Equations — returns relative hazard vs the mean PCE participant.
 * 1.0 = average risk for that sex/race stratum.
 *
 * features: { age, sex, race, total_cholesterol, hdl, systolic_bp,
 *             smoker ("never"|"former"|"current"), diabetes, bp_treated }
 */
export function predictAscvdHazard(features) {
  const age  = Math.max(40, Math.min(79, parseFloat(features.age  || 55)));
  const sex  = (features.sex  || 'male').toLowerCase();
  const race = (features.race || 'white').toLowerCase();
  const tc   = parseFloat(features.total_cholesterol || 200);
  const hdl  = parseFloat(features.hdl               || 50);
  const sbp  = parseFloat(features.systolic_bp       || 120);
  const bpTreated = !!features.bp_treated;
  const diabetes  = !!features.diabetes;
  const smoker    = (features.smoker || 'never').toLowerCase() === 'current';

  const raceKey = race === 'aa' ? 'aa' : 'white';
  let stratum = `${raceKey}_${sex}`;
  if (!_PCE[stratum]) stratum = `white_${sex}`;

  const s = _pceSum(age, tc, hdl, sbp, smoker, diabetes, bpTreated, stratum);
  return Math.exp(s - _PCE[stratum].meanCoef);
}

/**
 * 10-year ASCVD risk as a probability (0–1).
 */
export function ascvd10yrRisk(features) {
  const age  = Math.max(40, Math.min(79, parseFloat(features.age  || 55)));
  const sex  = (features.sex  || 'male').toLowerCase();
  const race = (features.race || 'white').toLowerCase();
  const raceKey = race === 'aa' ? 'aa' : 'white';
  let stratum = `${raceKey}_${sex}`;
  if (!_PCE[stratum]) stratum = `white_${sex}`;
  const rh = predictAscvdHazard(features);
  return Math.max(0, Math.min(1, 1 - Math.pow(_PCE[stratum].baselineS10, rh)));
}

// ─────────────────────────────────────────────────────────────────────────────
// Layer 3 — VO2 Max fitness percentile
// ─────────────────────────────────────────────────────────────────────────────

function _vo2PercentileBucket(age, sex) {
  if (!_vo2Norms) return null;
  const norms = _vo2Norms.norms[sex] || _vo2Norms.norms.male;
  return norms.find(b => age >= b.age_min && age <= b.age_max) || norms[norms.length - 1];
}

/**
 * Returns VO2 quartile string: "bottom_25" | "p25_to_50" | "p50_to_75" | "top_25"
 */
export function vo2PercentileBucket(age, sex, vo2) {
  const b = _vo2PercentileBucket(Math.round(age), (sex || 'male').toLowerCase());
  if (!b) return 'bottom_25';
  if (vo2 < b.p25) return 'bottom_25';
  if (vo2 < b.p50) return 'p25_to_50';
  if (vo2 < b.p75) return 'p50_to_75';
  return 'top_25';
}

/**
 * VO2 Max hazard modifier. Returns 1.0 (neutral) when vo2Max is null/undefined.
 */
export function predictVo2Hazard(age, sex, vo2Max) {
  if (vo2Max == null) return 1.0;
  if (!_vo2Norms) return 1.0;
  const bucket = vo2PercentileBucket(age, sex, parseFloat(vo2Max));
  return _vo2Norms.hazard_ratios[bucket] ?? 1.0;
}

// ─────────────────────────────────────────────────────────────────────────────
// Layer 5 — Grip strength / dead hang
// Source: Lancet PURE study (Leong et al. 2015)
// ─────────────────────────────────────────────────────────────────────────────

const _GRIP_MEDIAN = { male: 46.0, female: 28.0 };

const _HANG_BUCKETS = {
  male:   [[0, 20], [20, 45], [45, 90], [90, Infinity]],
  female: [[0, 14], [14, 32], [32, 63], [63, Infinity]],
};

const _HANG_GRIP_FRAC = [0.68, 0.87, 1.05, 1.25];

function _gripHrFromKg(sex, gripKg) {
  const median  = _GRIP_MEDIAN[sex] ?? 46.0;
  const deficit = Math.max(0, median - gripKg);
  return Math.pow(1.16, deficit / 5.0);
}

/**
 * Grip strength hazard modifier.
 * Priority: gripKg > hangSeconds. Returns 1.0 when neither is supplied.
 */
export function predictGripHazard(sex, gripKg = null, hangSeconds = null) {
  sex = (sex || 'male').toLowerCase();
  if (gripKg != null) return _gripHrFromKg(sex, parseFloat(gripKg));
  if (hangSeconds != null) {
    const buckets = _HANG_BUCKETS[sex] || _HANG_BUCKETS.male;
    const idx = buckets.findIndex(([lo, hi]) => hangSeconds >= lo && hangSeconds < hi);
    const frac = _HANG_GRIP_FRAC[idx >= 0 ? idx : buckets.length - 1];
    return _gripHrFromKg(sex, (_GRIP_MEDIAN[sex] ?? 46) * frac);
  }
  return 1.0;
}

// ─────────────────────────────────────────────────────────────────────────────
// Combined hazard (Layers 2 + 3 + 5)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Multiply all available hazard layers. Missing inputs contribute HR = 1.0.
 *
 * features: { age, sex, race, total_cholesterol, hdl, systolic_bp,
 *             smoker, diabetes, bp_treated, vo2_max, grip_kg, hang_seconds }
 */
export function predictCombinedHazard(features) {
  const age = parseFloat(features.age || 55);
  const sex = (features.sex || 'male').toLowerCase();

  const ascvdH = predictAscvdHazard(features);                         // Layer 2
  const vo2H   = predictVo2Hazard(age, sex, features.vo2_max ?? null); // Layer 3
  const gripH  = predictGripHazard(                                     // Layer 5
    sex,
    features.grip_kg      ?? null,
    features.hang_seconds ?? null,
  );
  // TODO: autonomic modifier (HRV + resting HR) — not yet wired
  // TODO: CRP modifier — not yet wired
  return ascvdH * vo2H * gripH;
}

// ─────────────────────────────────────────────────────────────────────────────
// Risk factor evaluation
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Evaluate each input against clinical reference ranges.
 * Returns { positive: [{label, impact}], negative: [{label, impact}] }
 * impact is "high" | "medium" | "low"
 */
export function evaluateRiskFactors(features) {
  const positive = [], negative = [];
  const age = parseFloat(features.age || 55);
  const sex = (features.sex || 'male').toLowerCase();

  // LDL
  const ldl = features.ldl != null ? parseFloat(features.ldl) : null;
  if (ldl != null) {
    if      (ldl < 100) positive.push({ label: `LDL ${ldl.toFixed(0)} — optimal`,      impact: 'high' });
    else if (ldl < 130) positive.push({ label: `LDL ${ldl.toFixed(0)} — near optimal`,  impact: 'high' });
    else                negative.push({ label: `LDL ${ldl.toFixed(0)} — elevated`,      impact: 'high' });
  }

  // HDL
  const hdl = features.hdl != null ? parseFloat(features.hdl) : null;
  if (hdl != null) {
    if      (hdl >= 60) positive.push({ label: `HDL ${hdl.toFixed(0)} — protective`,    impact: 'high' });
    else if (hdl >= 40) positive.push({ label: `HDL ${hdl.toFixed(0)} — acceptable`,    impact: 'high' });
    else                negative.push({ label: `HDL ${hdl.toFixed(0)} — below optimal`, impact: 'high' });
  }

  // ApoB
  const apob = features.apob != null ? parseFloat(features.apob) : null;
  if (apob != null) {
    if      (apob < 80) positive.push({ label: `ApoB ${apob.toFixed(0)} — optimal`,    impact: 'high' });
    else if (apob < 90) negative.push({ label: `ApoB ${apob.toFixed(0)} — borderline`, impact: 'high' });
    else                negative.push({ label: `ApoB ${apob.toFixed(0)} — elevated`,   impact: 'high' });
  }

  // HbA1c
  const hba1c = features.hba1c != null ? parseFloat(features.hba1c) : null;
  if (hba1c != null) {
    if      (hba1c < 5.7) positive.push({ label: `HbA1c ${hba1c.toFixed(1)}% — excellent`,       impact: 'high' });
    else if (hba1c < 6.4) negative.push({ label: `HbA1c ${hba1c.toFixed(1)}% — prediabetic range`, impact: 'high' });
    else                  negative.push({ label: `HbA1c ${hba1c.toFixed(1)}% — diabetic range`,    impact: 'high' });
  }

  // CRP (crp_raw)
  const crp = features.crp_raw != null ? parseFloat(features.crp_raw) : null;
  if (crp != null) {
    if      (crp <= 1.0) positive.push({ label: `CRP ${crp.toFixed(1)} — low inflammation`,      impact: 'medium' });
    else if (crp <= 3.0) negative.push({ label: `CRP ${crp.toFixed(1)} — moderate inflammation`, impact: 'medium' });
    else                 negative.push({ label: `CRP ${crp.toFixed(1)} — elevated inflammation`,  impact: 'medium' });
  }

  // Triglycerides
  const trig = features.triglycerides != null ? parseFloat(features.triglycerides) : null;
  if (trig != null) {
    if      (trig < 100) positive.push({ label: `Triglycerides ${trig.toFixed(0)} — optimal`,    impact: 'medium' });
    else if (trig < 150) positive.push({ label: `Triglycerides ${trig.toFixed(0)} — acceptable`, impact: 'medium' });
    else                 negative.push({ label: `Triglycerides ${trig.toFixed(0)} — elevated`,    impact: 'medium' });
  }

  // Systolic BP
  const sbp = features.systolic_bp != null ? parseFloat(features.systolic_bp) : null;
  if (sbp != null) {
    if      (sbp < 120) positive.push({ label: `Blood pressure ${sbp.toFixed(0)} — optimal`,  impact: 'high' });
    else if (sbp < 130) negative.push({ label: `Blood pressure ${sbp.toFixed(0)} — elevated`, impact: 'high' });
    else                negative.push({ label: `Blood pressure ${sbp.toFixed(0)} — high`,      impact: 'high' });
  }

  // BMI
  const bmi = features.bmi != null ? parseFloat(features.bmi) : null;
  if (bmi != null) {
    if      (bmi >= 18.5 && bmi < 25) positive.push({ label: `BMI ${bmi.toFixed(1)} — healthy range`,    impact: 'medium' });
    else if (bmi < 18.5)              negative.push({ label: `BMI ${bmi.toFixed(1)} — underweight`,       impact: 'medium' });
    else if (bmi < 30)                negative.push({ label: `BMI ${bmi.toFixed(1)} — slightly elevated`, impact: 'medium' });
    else                              negative.push({ label: `BMI ${bmi.toFixed(1)} — obese range`,       impact: 'medium' });
  }

  // Resting HR
  const rhr = features.resting_hr != null ? parseFloat(features.resting_hr) : null;
  if (rhr != null) {
    if      (rhr < 60) positive.push({ label: `Resting HR ${rhr.toFixed(0)} bpm — excellent`, impact: 'medium' });
    else if (rhr < 80) positive.push({ label: `Resting HR ${rhr.toFixed(0)} bpm — normal`,    impact: 'medium' });
    else               negative.push({ label: `Resting HR ${rhr.toFixed(0)} bpm — elevated`,  impact: 'medium' });
  }

  // Fasting glucose
  const glucose = features.blood_glucose != null ? parseFloat(features.blood_glucose) : null;
  if (glucose != null) {
    if      (glucose < 90)  positive.push({ label: `Glucose ${glucose.toFixed(0)} — optimal`,       impact: 'medium' });
    else if (glucose < 100) positive.push({ label: `Glucose ${glucose.toFixed(0)} — normal`,        impact: 'medium' });
    else                    negative.push({ label: `Glucose ${glucose.toFixed(0)} — above optimal`,  impact: 'medium' });
  }

  // Smoking
  const smoker = (features.smoker || 'never').toLowerCase();
  if      (smoker === 'never')   positive.push({ label: 'Non-smoker — no smoking risk',          impact: 'high'   });
  else if (smoker === 'former')  negative.push({ label: 'Former smoker — residual risk',         impact: 'medium' });
  else                           negative.push({ label: 'Current smoker — significant risk',      impact: 'high'   });

  // VO2 Max
  const vo2 = features.vo2_max != null ? parseFloat(features.vo2_max) : null;
  if (vo2 != null) {
    const bucket = vo2PercentileBucket(age, sex, vo2);
    const pctMap = { bottom_25: 12, p25_to_50: 37, p50_to_75: 62, top_25: 87 };
    const pct = pctMap[bucket];
    if      (pct >= 75) positive.push({ label: `VO2 Max ${vo2.toFixed(0)} — top 25%`,      impact: 'high'   });
    else if (pct >= 50) positive.push({ label: `VO2 Max ${vo2.toFixed(0)} — above average`, impact: 'medium' });
    else if (pct >= 25) negative.push({ label: `VO2 Max ${vo2.toFixed(0)} — below average`, impact: 'medium' });
    else                negative.push({ label: `VO2 Max ${vo2.toFixed(0)} — bottom 25%`,    impact: 'high'   });
  } else {
    negative.push({ label: 'VO2 Max not entered — high impact field', impact: 'high' });
  }

  // Grip strength
  const gripKg = features.grip_kg != null ? parseFloat(features.grip_kg) : null;
  const hangS  = features.hang_seconds != null ? parseFloat(features.hang_seconds) : null;
  const gripPct = _gripQuartileApprox(sex, gripKg, hangS);
  if (gripPct != null) {
    if      (gripPct >= 75) positive.push({ label: 'Grip strength — top 25%',      impact: 'high'   });
    else if (gripPct >= 50) positive.push({ label: 'Grip strength — above average', impact: 'medium' });
    else if (gripPct >= 25) negative.push({ label: 'Grip strength — below average', impact: 'medium' });
    else                    negative.push({ label: 'Grip strength — bottom 25%',    impact: 'high'   });
  } else {
    negative.push({ label: 'Grip strength not entered — high impact field', impact: 'high' });
  }

  // HRV
  const hrv = features.hrv != null ? parseFloat(features.hrv) : null;
  if (hrv != null) {
    if      (hrv >= 50) positive.push({ label: `HRV ${hrv.toFixed(0)}ms — excellent`,                 impact: 'medium' });
    else if (hrv >= 30) positive.push({ label: `HRV ${hrv.toFixed(0)}ms — normal`,                    impact: 'low'    });
    else                negative.push({ label: `HRV ${hrv.toFixed(0)}ms — low autonomic flexibility`, impact: 'medium' });
  }

  return { positive, negative };
}

function _gripQuartileApprox(sex, gripKg, hangSeconds) {
  sex = (sex || 'male').toLowerCase();
  if (gripKg != null) {
    const g = parseFloat(gripKg);
    if (sex === 'female') {
      if (g < 23) return 12; if (g < 28) return 37; if (g < 33) return 62; return 87;
    } else {
      if (g < 38) return 12; if (g < 46) return 37; if (g < 54) return 62; return 87;
    }
  }
  if (hangSeconds != null) {
    const buckets = _HANG_BUCKETS[sex] || _HANG_BUCKETS.male;
    const idx = buckets.findIndex(([lo, hi]) => hangSeconds >= lo && hangSeconds < hi);
    return [12, 37, 62, 87][idx >= 0 ? idx : buckets.length - 1];
  }
  return null;
}

// ─────────────────────────────────────────────────────────────────────────────
// Inline console tests — call runTests() after init() in browser console
// ─────────────────────────────────────────────────────────────────────────────

export function runTests() {
  let passed = 0, failed = 0;

  function assert(name, got, expected, tol = 0.001) {
    const ok = Math.abs(got - expected) <= tol;
    const status = ok ? 'PASS' : 'FAIL';
    if (ok) passed++; else failed++;
    console.log(`${status}  ${name}`);
    if (!ok) console.warn(`       got ${got}, expected ${expected} (tol ${tol})`);
  }
  function assertRange(name, got, lo, hi) {
    const ok = got >= lo && got <= hi;
    const status = ok ? 'PASS' : 'FAIL';
    if (ok) passed++; else failed++;
    console.log(`${status}  ${name}`);
    if (!ok) console.warn(`       got ${got}, expected [${lo}, ${hi}]`);
  }

  console.group('health_models.js tests');

  // ── Test 1: ASCVD 55yo white male, TC=200, HDL=50, SBP=120, never, no DM
  // Ground truth from Python: rel_hazard=0.5743, 10yr_risk=5.01%
  const f1 = { age: 55, sex: 'male', race: 'white', total_cholesterol: 200,
               hdl: 50, systolic_bp: 120, smoker: 'never', diabetes: false, bp_treated: false };
  assert('ASCVD 55yo male avg — rel_hazard', predictAscvdHazard(f1), 0.574299, 0.001);
  assert('ASCVD 55yo male avg — 10yr risk', ascvd10yrRisk(f1), 0.0501, 0.002);

  // ── Test 2: ASCVD 65yo white female, TC=250, HDL=45, SBP=150, current smoker, DM
  // Ground truth from Python: rel_hazard=18.958, 10yr_risk=47.58%
  const f2 = { age: 65, sex: 'female', race: 'white', total_cholesterol: 250,
               hdl: 45, systolic_bp: 150, smoker: 'current', diabetes: true, bp_treated: false };
  assert('ASCVD 65yo female high risk — rel_hazard', predictAscvdHazard(f2), 18.958, 0.05);
  assert('ASCVD 65yo female high risk — 10yr risk',  ascvd10yrRisk(f2),      0.4758, 0.005);

  // ── Test 3: 66yo male baseline — median remaining years plausible
  // CDC says ~17 years for a 66yo US male; Python gives 17.1
  const curve66 = integrateSurvival(66, 'male', 1.0, 110);
  const s66 = summarizeSurvival(curve66, 66);
  assertRange('66yo male baseline — 5yr risk',          s66.risk5 * 100,       8, 16);
  assertRange('66yo male baseline — 10yr risk',         s66.risk10 * 100,      18, 32);
  assertRange('66yo male baseline — median remaining',  s66.medianYears,       14, 21);

  // ── Test 4: Gompertz — smooth tail, no cliff at age 99
  const agesAbove100 = curve66.filter(r => r.age >= 100);
  const hValues = agesAbove100.map(r => r.h);
  const isMonotone = hValues.every((h, i) => i === 0 || h >= hValues[i - 1]);
  if (isMonotone && hValues.length > 5) {
    passed++;
    console.log(`PASS  Gompertz tail — monotone increasing mx from age 100 to ${Math.max(...agesAbove100.map(r => r.age))}`);
  } else {
    failed++;
    console.warn('FAIL  Gompertz tail — not monotone or too few points:', hValues);
  }
  // Cliff test: h should never jump more than 5x between consecutive ages
  const allH = curve66.map(r => r.h);
  const hasCliff = allH.some((h, i) => i > 0 && h > allH[i - 1] * 5);
  if (!hasCliff) {
    passed++;
    console.log('PASS  No cliff (no >5x jump in h between consecutive ages)');
  } else {
    failed++;
    const cliff = allH.findIndex((h, i) => i > 0 && h > allH[i - 1] * 5);
    console.warn(`FAIL  Cliff detected at age ${curve66[cliff]?.age}: h jumped from ${allH[cliff-1]?.toFixed(3)} to ${allH[cliff]?.toFixed(3)}`);
  }

  // ── Test 5: VO2 hazard
  assert('VO2 hazard — bottom_25 (male 50yo, VO2=25)', predictVo2Hazard(50, 'male', 25), 1.00);
  assert('VO2 hazard — top_25 (male 50yo, VO2=55)',    predictVo2Hazard(50, 'male', 55), 0.25);
  assert('VO2 hazard — null (neutral)',                 predictVo2Hazard(50, 'male', null), 1.0);

  // ── Test 6: Grip hazard
  assert('Grip hazard — male at median (46kg)',  predictGripHazard('male', 46),   1.00, 0.001);
  assert('Grip hazard — male low (30kg)',        predictGripHazard('male', 30),   1.608, 0.005);
  assert('Grip hazard — null (neutral)',         predictGripHazard('male', null), 1.00);

  // ── Test 7: Dead hang converts to grip
  const hangH = predictGripHazard('male', null, 60); // 45-90s = 50-75th percentile
  assertRange('Grip hazard — male dead hang 60s', hangH, 0.8, 1.1);

  console.groupEnd();
  console.log(`\nResult: ${passed} passed, ${failed} failed`);
  return { passed, failed };
}
