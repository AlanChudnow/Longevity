/**
 * apple_health.js — JS translation of src/apple_health.py
 *
 * Streams export.xml via the browser File/ReadableStream API + sax-js.
 * Never loads the full file into memory.
 *
 * Requires sax.min.js loaded as a plain <script> tag (sets globalThis.sax).
 *
 * windowMode: "last_value" | "last_week" | "last_month"
 * asOf:       Date | null — if set, records after this point are ignored and
 *             window calculations are relative to this date.
 */

// ─────────────────────────────────────────────────────────────────────────────
// Identifier sets (HK type strings)
// ─────────────────────────────────────────────────────────────────────────────

const _WANT = new Set([
  'HKQuantityTypeIdentifierBodyMass',
  'HKQuantityTypeIdentifierHeight',
  'HKQuantityTypeIdentifierBloodPressureSystolic',
  'HKQuantityTypeIdentifierRestingHeartRate',
  'HKQuantityTypeIdentifierHeartRateVariabilitySDNN',
  'HKQuantityTypeIdentifierVO2Max',
  'HKQuantityTypeIdentifierBloodGlucose',
]);

const _AVG_OK = new Set([
  'HKQuantityTypeIdentifierBodyMass',
  'HKQuantityTypeIdentifierBloodPressureSystolic',
  'HKQuantityTypeIdentifierRestingHeartRate',
  'HKQuantityTypeIdentifierHeartRateVariabilitySDNN',
  'HKQuantityTypeIdentifierVO2Max',
  'HKQuantityTypeIdentifierBloodGlucose',
]);

const _ID_TO_KEY = {
  'HKQuantityTypeIdentifierBodyMass':              'weight_lb',
  'HKQuantityTypeIdentifierBloodPressureSystolic': 'systolic_bp',
  'HKQuantityTypeIdentifierRestingHeartRate':      'resting_hr',
  'HKQuantityTypeIdentifierHeartRateVariabilitySDNN': 'hrv',
  'HKQuantityTypeIdentifierVO2Max':                'vo2_max',
  'HKQuantityTypeIdentifierBloodGlucose':          'blood_glucose',
};

// ─────────────────────────────────────────────────────────────────────────────
// Unit conversions
// ─────────────────────────────────────────────────────────────────────────────

function _toKg(v, unit) {
  const u = unit.toLowerCase();
  return (u === 'lb' || u === 'lbs') ? v * 0.453592 : v;
}

function _toInches(v, unit) {
  const u = unit.toLowerCase();
  if (u === 'cm' || u === 'cm^1') return v / 2.54;
  if (u === 'ft') return v * 12.0;
  return v;
}

function _toMgDl(v, unit) {
  return unit.toLowerCase() === 'mmol/l' ? v * 18.016 : v;
}

// ─────────────────────────────────────────────────────────────────────────────
// Date helpers
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Parse Apple Health date string to a numeric timestamp (ms).
 * Timezone info is stripped (treated as naive/local), matching Python behavior.
 * Supports: "YYYY-MM-DD HH:MM:SS ±HHMM", "YYYY-MM-DD HH:MM:SS", "YYYY-MM-DD"
 */
function _parseDt(s) {
  if (!s) return null;
  const m = s.trim().match(/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}):(\d{2}))?/);
  if (!m) return null;
  // Use Date.UTC with local-time components to strip timezone (matches Python .replace(tzinfo=None))
  return Date.UTC(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0), +(m[6] || 0));
}

/**
 * Convert a Date object to a "naive" timestamp using UTC component extraction.
 * Used so asOf comparisons are consistent with _parseDt's timezone-stripping.
 */
function _dateToNaiveMs(d) {
  return Date.UTC(
    d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate(),
    d.getUTCHours(), d.getUTCMinutes(), d.getUTCSeconds(),
  );
}

function _ageFromDob(dobStr) {
  if (!dobStr) return null;
  const m = dobStr.slice(0, 10).match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!m) return null;
  const [, yr, mo, dy] = m.map(Number);
  const today = new Date();
  let age = today.getUTCFullYear() - yr;
  if (today.getUTCMonth() + 1 < mo ||
      (today.getUTCMonth() + 1 === mo && today.getUTCDate() < dy)) {
    age--;
  }
  return age;
}

// ─────────────────────────────────────────────────────────────────────────────
// Streaming XML parser
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Stream file through sax-js, calling onOpenTag(name, attributes) for each element.
 * onProgress(fraction) is called after each chunk (0.0–1.0), optional.
 */
async function _streamParse(file, onOpenTag, onProgress) {
  const saxParser = globalThis.sax.parser(true /* strict */, {});
  saxParser.onopentag = (node) => onOpenTag(node.name, node.attributes);
  // Resume on parse errors (e.g. unknown entities in DOCTYPE) rather than halting
  saxParser.onerror = () => saxParser.resume();

  const reader = file.stream().getReader();
  const decoder = new TextDecoder('utf-8');
  let bytesRead = 0;

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      bytesRead += value.length;
      saxParser.write(decoder.decode(value, { stream: true }));
      if (onProgress) onProgress(bytesRead / file.size);
      // Yield to keep the browser responsive during a large parse
      await new Promise(r => setTimeout(r, 0));
    }
    saxParser.close();
  } finally {
    reader.releaseLock();
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Public API
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Parse Apple Health export.xml and return biometric values.
 *
 * @param {File}   file        Browser File object (export.xml)
 * @param {string} windowMode  "last_value" | "last_week" | "last_month"
 * @param {Date|null} asOf     If set, records after this date are ignored
 * @param {Function|null} onProgress  Called with (fraction: 0–1) per chunk
 *
 * @returns {Promise<{
 *   age, sex,
 *   weight_lb, weight_kg, height_in, bmi,
 *   systolic_bp, resting_hr, hrv, vo2_max, blood_glucose,
 *   _counts, _date_range
 * }>}
 * All numeric values are null when absent.
 */
export async function parseAppleHealth(file, windowMode = 'last_value', asOf = null, onProgress = null) {
  const nowMs  = asOf instanceof Date ? _dateToNaiveMs(asOf) : Date.now();
  const cutoffMs = windowMode === 'last_week'  ? nowMs - 7  * 86400000
                 : windowMode === 'last_month' ? nowMs - 30 * 86400000
                 : null;

  // latest[identifier] = { dt, value, unit }  — most recent record
  const latest   = {};
  // windowed[identifier] = [{ dt, value, unit }, ...]  — readings in window
  const windowed = {};

  let dobStr = null;
  let sexRaw = null;
  let minDt  = null;
  let maxDt  = null;

  await _streamParse(file, (name, attrs) => {
    if (name === 'Me') {
      dobStr = attrs['HKCharacteristicTypeIdentifierDateOfBirth'] || null;
      sexRaw = attrs['HKCharacteristicTypeIdentifierBiologicalSex'] || null;
      return;
    }
    if (name !== 'Record') return;

    const dt = _parseDt(attrs['endDate'] || '');

    // Track full date range before any as_of filtering
    if (dt !== null) {
      if (minDt === null || dt < minDt) minDt = dt;
      if (maxDt === null || dt > maxDt) maxDt = dt;
    }

    const recType = attrs['type'] || '';
    if (!_WANT.has(recType)) return;

    // Skip records after the as_of cutoff
    if (asOf !== null && dt !== null && dt > nowMs) return;

    const value = parseFloat(attrs['value'] || '');
    if (isNaN(value)) return;
    const unit = attrs['unit'] || '';

    if (dt !== null) {
      // Track most-recent reading
      const prev = latest[recType];
      if (!prev || dt > prev.dt) {
        latest[recType] = { dt, value, unit };
      }
      // Collect windowed readings
      if (cutoffMs !== null && _AVG_OK.has(recType) && dt >= cutoffMs) {
        if (!windowed[recType]) windowed[recType] = [];
        windowed[recType].push({ dt, value, unit });
      }
    }
  }, onProgress);

  // ── Build effective values (windowed average or latest single reading) ──────

  const counts = {};

  function _get(identifier) {
    if (cutoffMs !== null && _AVG_OK.has(identifier)) {
      const readings = windowed[identifier] || [];
      if (readings.length) {
        const fieldKey = _ID_TO_KEY[identifier];
        let vals, canonUnit;
        if (identifier === 'HKQuantityTypeIdentifierBodyMass') {
          vals = readings.map(r => _toKg(r.value, r.unit));
          canonUnit = 'kg';
        } else if (identifier === 'HKQuantityTypeIdentifierBloodGlucose') {
          vals = readings.map(r => _toMgDl(r.value, r.unit));
          canonUnit = 'mg/dL';
        } else {
          vals = readings.map(r => r.value);
          canonUnit = readings[readings.length - 1].unit;
        }
        const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
        if (fieldKey) counts[fieldKey] = readings.length;
        return { dt: readings[readings.length - 1].dt, value: avg, unit: canonUnit };
      }
    }
    return latest[identifier] || null;
  }

  // Weight
  let weightKg = null, weightLb = null;
  const wt = _get('HKQuantityTypeIdentifierBodyMass');
  if (wt) {
    weightKg = _toKg(wt.value, wt.unit);   // _get already converts to kg for windowed
    weightLb = weightKg / 0.453592;
  }

  // Height — always most recent; no averaging
  let heightIn = null;
  const ht = latest['HKQuantityTypeIdentifierHeight'];
  if (ht) heightIn = _toInches(ht.value, ht.unit);

  // BMI
  let bmi = null;
  if (weightLb !== null && heightIn !== null && heightIn > 0) {
    bmi = (weightLb / (heightIn ** 2)) * 703.0;
  }

  // Systolic BP
  let systolicBp = null;
  const bp = _get('HKQuantityTypeIdentifierBloodPressureSystolic');
  if (bp) systolicBp = bp.value;

  // Resting HR
  let restingHr = null;
  const rhr = _get('HKQuantityTypeIdentifierRestingHeartRate');
  if (rhr) restingHr = rhr.value;

  // HRV
  let hrv = null;
  const hrvEntry = _get('HKQuantityTypeIdentifierHeartRateVariabilitySDNN');
  if (hrvEntry) hrv = hrvEntry.value;

  // VO2 Max
  let vo2Max = null;
  const vo2 = _get('HKQuantityTypeIdentifierVO2Max');
  if (vo2) vo2Max = vo2.value;

  // Blood glucose
  let bloodGlucose = null;
  const bg = _get('HKQuantityTypeIdentifierBloodGlucose');
  if (bg) bloodGlucose = _toMgDl(bg.value, bg.unit);   // already mg/dL for windowed

  // Age + sex from <Me>
  const age = _ageFromDob(dobStr);
  let sex = null;
  if (sexRaw) {
    const s = sexRaw.toLowerCase();
    if (s.includes('female')) sex = 'female';
    else if (s.includes('male')) sex = 'male';
  }

  function _r(v, digits) {
    if (v === null) return null;
    const f = 10 ** digits;
    return Math.round(v * f) / f;
  }

  return {
    age,
    sex,
    weight_lb:     _r(weightLb, 1),
    weight_kg:     _r(weightKg, 2),
    height_in:     _r(heightIn, 1),
    bmi:           _r(bmi, 1),
    systolic_bp:   systolicBp   !== null ? Math.round(systolicBp)   : null,
    resting_hr:    restingHr    !== null ? Math.round(restingHr)    : null,
    hrv:           _r(hrv, 1),
    vo2_max:       _r(vo2Max, 1),
    blood_glucose: _r(bloodGlucose, 1),
    _counts:       counts,
    _date_range:   [minDt, maxDt],
  };
}

/**
 * Return [minDt, maxDt] (numeric timestamps) for all Record endDates.
 * Equivalent to Python's get_export_date_range(); used for the historical date picker.
 */
export async function getExportDateRange(file, onProgress = null) {
  let minDt = null;
  let maxDt = null;

  await _streamParse(file, (name, attrs) => {
    if (name !== 'Record') return;
    const dt = _parseDt(attrs['endDate'] || '');
    if (dt !== null) {
      if (minDt === null || dt < minDt) minDt = dt;
      if (maxDt === null || dt > maxDt) maxDt = dt;
    }
  }, onProgress);

  return [minDt, maxDt];
}
