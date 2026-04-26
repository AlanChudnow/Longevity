/**
 * lab_parser.js — JS translation of src/lab_parser.py
 * Extracts blood panel values from lab PDFs using pdf.js for text extraction.
 *
 * Supported vendors (auto-detected): Labcorp, Sonora Quest
 *
 * parseLabPdf(file) → Promise<{ total_cholesterol, hdl, ldl, non_hdl,
 *   vldl, triglycerides, apob, hba1c, glucose, crp, lab_vendor, lab_date }>
 * All numeric values are number | null.
 */

import * as pdfjsLib from '../lib/pdfjs/pdf.mjs';

// Configure worker — use absolute URL so it loads from any page depth
pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  '../lib/pdfjs/pdf.worker.mjs', import.meta.url
).href;

// ─────────────────────────────────────────────────────────────────────────────
// Text extraction from pdf.js
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Assemble pdf.js content items into newline-separated lines.
 * Items on the same y-coordinate (within tolerance) are joined left-to-right.
 */
function _assembleLines(items) {
  // Bucket items by rounded y (2-unit tolerance handles minor baseline shifts)
  const buckets = new Map();
  for (const item of items) {
    if (!item.str) continue;
    const y = Math.round(item.transform[5] / 2) * 2;
    if (!buckets.has(y)) buckets.set(y, []);
    buckets.get(y).push({ x: item.transform[4], str: item.str });
  }

  return [...buckets.entries()]
    .sort(([ya], [yb]) => yb - ya)          // higher y = higher on page
    .map(([, its]) =>
      its
        .sort((a, b) => a.x - b.x)         // left to right
        .map(i => i.str)
        .join(' ')
        .replace(/\s{2,}/g, ' ')
        .trim()
    )
    .filter(l => l.length > 0)
    .join('\n');
}

async function _extractFullText(file) {
  const buf  = await file.arrayBuffer();
  const pdf  = await pdfjsLib.getDocument({ data: buf }).promise;
  const pages = [];
  for (let p = 1; p <= pdf.numPages; p++) {
    const page    = await pdf.getPage(p);
    const content = await page.getTextContent();
    pages.push(_assembleLines(content.items));
  }
  return pages.join('\n');
}

// ─────────────────────────────────────────────────────────────────────────────
// Vendor detection
// ─────────────────────────────────────────────────────────────────────────────

function _detectLab(text) {
  const t = text.toLowerCase();
  if (t.includes('labcorp') || t.includes('laboratory corporation')) return 'labcorp';
  if (t.includes('sonora quest')) return 'sonora_quest';
  return 'unknown';
}

// ─────────────────────────────────────────────────────────────────────────────
// Label maps per vendor (same keys and variants as Python)
// ─────────────────────────────────────────────────────────────────────────────

function _labelsForLab(lab) {
  if (lab === 'labcorp') {
    return {
      hdl:               ['HDL Cholesterol 01', 'HDL Cholesterol'],
      ldl:               ['LDL Chol Calc (NIH)', 'LDL Cholesterol'],
      non_hdl:           ['Non-HDL Cholesterol'],
      vldl:              ['VLDL Cholesterol Cal', 'VLDL Cholesterol'],
      total_cholesterol: ['Cholesterol, Total 01', 'Cholesterol, Total'],
      triglycerides:     ['Triglycerides 01', 'Triglycerides'],
      apob:              ['Apolipoprotein B 01', 'Apolipoprotein B01', 'Apolipoprotein B'],
      hba1c:             ['Hemoglobin A1c 01', 'Hemoglobin A1c'],
      glucose:           ['Glucose 01', 'Glucose'],
      crp:               ['C-Reactive Protein, Quant 01', 'C-Reactive Protein'],
    };
  }
  if (lab === 'sonora_quest') {
    return {
      hdl:               ['HDL Cholesterol'],
      ldl:               ['LDL Cholesterol, Calculated', 'LDL Cholesterol'],
      non_hdl:           ['Non-HDL Cholesterol'],
      vldl:              ['VLDL Cholesterol'],
      total_cholesterol: ['Cholesterol'],
      triglycerides:     ['Triglycerides'],
      apob:              ['Apolipoprotein B'],
      hba1c:             ['Hemoglobin A1c'],
      glucose:           ['Glucose'],
      crp:               ['C-Reactive Protein'],
    };
  }
  // Unknown vendor — best-effort common labels
  return {
    hdl:               ['HDL Cholesterol', 'HDL-C', 'HDL Chol'],
    ldl:               ['LDL Cholesterol', 'LDL-C', 'LDL Chol'],
    non_hdl:           ['Non-HDL Cholesterol', 'Non HDL'],
    vldl:              ['VLDL Cholesterol', 'VLDL'],
    total_cholesterol: ['Total Cholesterol', 'Cholesterol, Total', 'Cholesterol'],
    triglycerides:     ['Triglycerides', 'Trig'],
    apob:              ['Apolipoprotein B', 'Apo B', 'ApoB'],
    hba1c:             ['Hemoglobin A1c', 'HbA1c', 'A1c'],
    glucose:           ['Glucose'],
    crp:               ['C-Reactive Protein', 'CRP'],
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Skip patterns — ratio rows, section headers, reference range tables
// ─────────────────────────────────────────────────────────────────────────────

const _SKIP_PATTERNS = [
  /cholesterol\/hdl/i,
  /bun\/creatinine/i,
  /albumin\/globulin/i,
  /values outside/i,
  /reference range/i,
  /risk categor/i,
  /\boptimal\b/i,
  /\bborderline\b/i,
  /near optimal/i,
];

function _shouldSkip(line) {
  return _SKIP_PATTERNS.some(p => p.test(line));
}

// ─────────────────────────────────────────────────────────────────────────────
// Vendor-specific preprocessing
// ─────────────────────────────────────────────────────────────────────────────

function _preprocess(text, lab) {
  if (lab === 'sonora_quest') {
    const idx = text.toLowerCase().indexOf('values outside of reference range');
    if (idx > 0) return text.slice(0, idx);
  }
  return text;
}

// ─────────────────────────────────────────────────────────────────────────────
// Number extraction (mirrors Python _extract_first_number exactly)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Extract the first numeric value from the text fragment after a matched label.
 *
 * Handles:
 *   "46 mg/dL >39"          → 46    (Labcorp HDL)
 *   "35 L ≤40 mg/dL"        → 35    (Sonora Quest HDL, flag L)
 *   "122 H * ≤99 mg/dL"     → 122   (Sonora Quest LDL)
 *   "5.1 % 4.8-5.6"         → 5.1   (Labcorp HbA1c)
 *   "5.2* ≤5.6 %"           → 5.2   (Sonora Quest HbA1c, asterisk glued)
 *   "96* 70 - 99 mg/dL"     → 96    (Sonora Quest Glucose)
 *   "<1 mg/L 0-10"          → 1     (CRP < prefix)
 *   "66 mg/dL <90"          → 66    (ApoB)
 */
function _extractFirstNumber(segment) {
  // Stop at reference range boundary: ≤ or ≥ always mark the ref range
  let seg = segment.split(/[≤≥≤≥]/)[0];

  // Strip glued asterisk after digit: "96*" → "96", "5.2*" → "5.2"
  seg = seg.replace(/(\d)\*/g, '$1');

  // Strip trailing flag letter (H, L, C) after digit+space: "35 L" → "35"
  seg = seg.replace(/(\d)\s+[HLChlc]\b/g, '$1');

  const m = seg.match(/\d+\.?\d*/);
  return m ? parseFloat(m[0]) : null;
}

// ─────────────────────────────────────────────────────────────────────────────
// Label matching (mirrors Python _match_label)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * If label appears in line as a whole-word match, return the portion after it.
 * Returns null if no match.
 */
function _matchLabel(line, label) {
  // Require label NOT immediately preceded by a letter or comma (word boundary)
  const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const pattern = new RegExp('(?<![A-Za-z,])' + escaped, 'i');
  const m = line.match(pattern);
  if (!m) return null;
  return line.slice(m.index + m[0].length);
}

// ─────────────────────────────────────────────────────────────────────────────
// Date extraction
// ─────────────────────────────────────────────────────────────────────────────

function _extractDate(text) {
  const m = text.match(/(?:date collected|collected)[:\s]+(\d{1,2}\/\d{1,2}\/\d{4})/i);
  return m ? m[1] : '';
}

// ─────────────────────────────────────────────────────────────────────────────
// Public API
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Parse a blood panel PDF file and return extracted values.
 *
 * @param {File} file  — browser File object (from drag-drop or file picker)
 * @returns {Promise<Object>} — { total_cholesterol, hdl, ldl, non_hdl, vldl,
 *   triglycerides, apob, hba1c, glucose, crp, lab_vendor, lab_date }
 */
export async function parseLabPdf(file) {
  const fullText = await _extractFullText(file);
  const lab = _detectLab(fullText);
  const workingText = _preprocess(fullText, lab);
  const lines = workingText.split('\n');
  const labelMap = _labelsForLab(lab);

  const results = Object.fromEntries(Object.keys(labelMap).map(k => [k, null]));

  for (const [key, variants] of Object.entries(labelMap)) {
    outer:
    for (const label of variants) {
      for (const line of lines) {
        if (_shouldSkip(line)) continue;
        const after = _matchLabel(line, label);
        if (after !== null) {
          const value = _extractFirstNumber(after);
          if (value !== null) {
            results[key] = value;
            break outer;
          }
        }
      }
    }
  }

  results.lab_vendor = lab;
  results.lab_date   = _extractDate(fullText);
  return results;
}

/**
 * Return the raw full text extracted from a PDF (for debugging).
 */
export async function extractRawText(file) {
  return _extractFullText(file);
}
