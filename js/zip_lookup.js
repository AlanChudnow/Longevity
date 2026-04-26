/**
 * zip_lookup.js — JS translation of geo_mx_offset / geo_zip_info from src/health_models.py
 *
 * Loads data/zip_life_expectancy.json on first use (lazy singleton).
 * Formula: hazard_multiplier = clamp(1.0 − offset_years × 0.02, 0.5, 2.0)
 */

let _zipMap = null;
let _initPromise = null;

export async function initZipLookup(dataPath = 'data') {
  if (_zipMap) return;
  if (_initPromise) return _initPromise;

  _initPromise = (async () => {
    const r = await fetch(`${dataPath}/zip_life_expectancy.json`);
    if (!r.ok) throw new Error(`ZIP data fetch failed: ${r.status}`);
    const arr = await r.json();
    _zipMap = new Map(arr.map(e => [e.zip, e]));
  })();

  return _initPromise;
}

/**
 * Return geographic metadata for a ZIP code.
 * { found, county_name, state_abbr, life_expectancy, offset }
 */
export function lookupZip(zip) {
  if (!zip || !_zipMap) return { found: false };
  const key = String(zip).trim().padStart(5, '0');
  const row = _zipMap.get(key);
  if (!row) return { found: false };
  return {
    found:           true,
    county_name:     row.county_name  || '',
    state_abbr:      row.state_abbr   || '',
    life_expectancy: row.life_expectancy,
    offset:          row.geo_mx_offset,
  };
}

/**
 * Return hazard multiplier for a ZIP code (1.0 = national average).
 * Matches Python: max(0.5, min(2.0, 1.0 − offset_years × 0.02))
 */
export function getGeoHazard(zip) {
  const info = lookupZip(zip);
  if (!info.found) return 1.0;
  return Math.max(0.5, Math.min(2.0, 1.0 - info.offset * 0.02));
}
