/**
 * chart.js — Phase 7 survival curve rendering with Chart.js
 * Translates _update_chart() from src/gui.py.
 *
 * Requires Chart.js loaded as a plain <script> (sets globalThis.Chart).
 */

let _chart = null;

/**
 * Render or re-render the survival curve chart.
 *
 * @param {string} canvasId
 * @param {object} opts
 *   userCurve      – [{age, S}, …] from integrateSurvival
 *   baselineCurve  – [{age, S}, …] from integrateSurvival with geo only
 *   age            – current age (integer)
 *   relHazard      – combined hazard (user)
 *   geo            – geographic hazard (baseline)
 *   risk5          – 5-yr mortality risk (0–1)
 *   risk10         – 10-yr mortality risk (0–1)
 *   medianYears    – median remaining years
 *   userLabel      – legend label for user curve
 *   baselineLabel  – legend label for population curve
 */
export function renderSurvivalChart(canvasId, {
  userCurve, baselineCurve, age, relHazard, geo,
  risk5, risk10, medianYears,
  userLabel     = 'You',
  baselineLabel = 'Population average',
}) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;

  // Show canvas, hide placeholder
  canvas.style.display = 'block';
  const ph = document.getElementById('chart-placeholder');
  if (ph) ph.style.display = 'none';

  if (_chart) { _chart.destroy(); _chart = null; }

  const ctx = canvas.getContext('2d');

  // Convert to "years from now" / survival %
  const ptUser = userCurve.map(p  => ({ x: p.age - age, y: +(p.S * 100).toFixed(3) }));
  const ptBase = baselineCurve.map(p => ({ x: p.age - age, y: +(p.S * 100).toFixed(3) }));
  const xMax   = Math.min(65, Math.max(
    ptUser[ptUser.length - 1]?.x ?? 60,
    ptBase[ptBase.length - 1]?.x ?? 60,
  ));

  // Design tokens
  const ACCENT = '#4a6fa5';
  const INK2   = '#3a4654';
  const INK3   = '#6b7a8a';
  const INK4   = '#9aa6b2';
  const LINE   = '#e7ebef';
  const OK     = '#3d9970';
  const WARN   = '#e67e22';

  // Fill between curves: green when user is better than baseline, orange when worse
  const userRelative = relHazard / geo;
  const fillAbove = userRelative <= 1.0 ? 'rgba(61,153,112,0.10)' : 'rgba(230,126,34,0.10)';
  const fillBelow = userRelative <= 1.0 ? 'rgba(230,126,34,0.10)' : 'rgba(61,153,112,0.10)';

  _chart = new globalThis.Chart(ctx, {
    type: 'line',
    data: {
      datasets: [
        {
          label:       `${userLabel}  (×${userRelative.toFixed(2)} vs baseline)`,
          data:        ptUser,
          borderColor: ACCENT,
          borderWidth: 2.5,
          pointRadius: 0,
          tension:     0.2,
          order:       1,
          fill:        { target: '+1', above: fillAbove, below: fillBelow },
        },
        {
          label:       baselineLabel,
          data:        ptBase,
          borderColor: INK4,
          borderWidth: 1.5,
          borderDash:  [5, 4],
          pointRadius: 0,
          tension:     0.2,
          order:       2,
          fill:        false,
        },
      ],
    },
    options: {
      responsive:          true,
      maintainAspectRatio: false,
      animation:           { duration: 450 },
      interaction:         { mode: 'index', intersect: false },
      plugins: {
        legend: {
          position: 'top',
          align:    'end',
          labels:   { color: INK2, font: { size: 11 }, usePointStyle: true, padding: 14 },
        },
        tooltip: {
          backgroundColor: '#fff',
          borderColor:     LINE,
          borderWidth:     1,
          titleColor:      INK2,
          bodyColor:       INK3,
          padding:         10,
          callbacks: {
            title:  items => `${items[0].parsed.x.toFixed(0)} yrs from now  (age ${age + +items[0].parsed.x.toFixed(0)})`,
            label:  item  => `${item.dataset.label.split('  ')[0]}: ${item.parsed.y.toFixed(1)}% survival`,
          },
        },
      },
      scales: {
        x: {
          type:  'linear',
          min:   0,
          max:   xMax,
          title: { display: true, text: 'Years from now', color: INK3, font: { size: 11 } },
          grid:  { color: LINE },
          ticks: { color: INK3, font: { size: 10 } },
        },
        y: {
          min:   0,
          max:   105,
          title: { display: true, text: 'Survival probability (%)', color: INK3, font: { size: 11 } },
          grid:  { color: LINE },
          ticks: { color: INK3, font: { size: 10 }, stepSize: 25, callback: v => v + '%' },
        },
      },
    },

    // Inline plugin: vertical marker lines + labels
    plugins: [{
      id: 'markers',
      afterDraw(chart) {
        const { ctx: c, chartArea: ca, scales } = chart;
        if (!ca) return;
        c.save();

        function vline(xYrs, color, dash, label, labelY) {
          const px = scales.x.getPixelForValue(xYrs);
          if (px < ca.left || px > ca.right) return;
          c.beginPath();
          c.setLineDash(dash);
          c.strokeStyle = color;
          c.lineWidth   = 1.2;
          c.moveTo(px, ca.top);
          c.lineTo(px, ca.bottom);
          c.stroke();
          c.setLineDash([]);
          if (label) {
            c.fillStyle = color;
            c.font      = '10px system-ui, sans-serif';
            c.textAlign = 'left';
            c.fillText(label, px + 3, Math.min(labelY, ca.bottom - 8));
          }
        }

        const yOf = pct => scales.y.getPixelForValue(pct);

        if (risk5 > 0)
          vline(5,  WARN,   [3, 3], `5-yr: ${(risk5  * 100).toFixed(1)}%`,
                Math.max(ca.top + 14, yOf((1 - risk5)  * 100) - 8));

        if (risk10 > 0)
          vline(10, ACCENT, [3, 3], `10-yr: ${(risk10 * 100).toFixed(1)}%`,
                Math.max(ca.top + 28, yOf((1 - risk10) * 100) - 8));

        if (medianYears > 0 && medianYears <= xMax)
          vline(medianYears, OK, [6, 3],
                `Median: ${medianYears.toFixed(0)} yrs`, ca.bottom - 44);

        c.restore();
      },
    }],
  });
}
