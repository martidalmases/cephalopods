/* Trend and significance, the same method everywhere on the site and in the backend
   (backend/tendencias.py):
     1. Sen's slope: the median of the slopes between every pair of years.
     2. Modified Mann-Kendall test (Hamed and Rao, 1998): Mann-Kendall with the variance corrected for
        the autocorrelation of the series (ranks of the detrended series, significant lags up to n/4;
        the correction can only make the test stricter).
     3. Benjamini-Hochberg (FDR) adjustment when many tests are made at once.
   Used here for selections drawn by the visitor (an area average); the maps come already computed. */
(function () {
"use strict";

var MIN_YEARS = 8, Z975 = 1.959963984540054;

function has(x) { return x !== null && x !== undefined && isFinite(x); }
function median(a) { a.sort(function (x, y) { return x - y; }); var n = a.length, h = n >> 1; return n % 2 ? a[h] : (a[h - 1] + a[h]) / 2; }
function normSf(z) {                          // upper tail of the standard normal, z >= 0
  var x = z / Math.SQRT2, t = 1 / (1 + 0.3275911 * x);
  var erfc = ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x);
  return 0.5 * erfc;
}
function ranks(v) {                           // average ranks for ties
  var idx = v.map(function (x, i) { return i; }).sort(function (a, b) { return v[a] - v[b]; }), r = new Array(v.length), i = 0, j, k;
  while (i < idx.length) { j = i; while (j + 1 < idx.length && v[idx[j + 1]] === v[idx[i]]) j++; for (k = i; k <= j; k++) r[idx[k]] = (i + j) / 2 + 1; i = j + 1; }
  return r;
}

/* v: values (null = missing), one per time step; t: optional times (default 0, 1, 2...).
   Returns { slope (per 10 time units), p, tau, n, mid, mean }: the Sen line passes through (mid, mean). */
function sen(v, t) {
  var x = [], y = [], k, i, j;
  for (k = 0; k < v.length; k++) if (has(v[k])) { x.push(t ? t[k] : k); y.push(v[k]); }
  var n = y.length;
  if (n < MIN_YEARS) return { slope: NaN, p: 1, tau: NaN, n: n, mid: NaN, mean: NaN };
  var slopes = [], s = 0, d;
  for (i = 0; i < n - 1; i++) for (j = i + 1; j < n; j++) { d = y[j] - y[i]; s += d > 0 ? 1 : d < 0 ? -1 : 0; slopes.push(d / (x[j] - x[i])); }
  var b = median(slopes), ties = 0, c;
  for (i = 0; i < n; i++) { c = 0; for (j = 0; j < n; j++) if (y[j] === y[i]) c++; ties += (c - 1) * (2 * c + 5); }
  var variance = (n * (n - 1) * (2 * n + 5) - ties) / 18;

  var r = ranks(y.map(function (yy, q) { return yy - b * x[q]; })), mr = 0, den = 0, sum = 0, lim = Z975 / Math.sqrt(n), rho, num;
  for (i = 0; i < n; i++) mr += r[i]; mr /= n;
  for (i = 0; i < n; i++) { r[i] -= mr; den += r[i] * r[i]; }
  for (k = 1; k <= Math.max(1, Math.floor(n / 4)); k++) {
    num = 0; for (i = k; i < n; i++) num += r[i] * r[i - k];
    rho = den > 0 ? num / den : 0;
    if (Math.abs(rho) > lim) sum += (n - k) * (n - k - 1) * (n - k - 2) * rho;
  }
  variance *= Math.max(1, 1 + 2 * sum / (n * (n - 1) * (n - 2)));
  var z = variance > 0 ? (s - (s > 0 ? 1 : s < 0 ? -1 : 0)) / Math.sqrt(variance) : 0;

  var mid = 0; for (i = 0; i < n; i++) mid += x[i]; mid /= n;
  var mean = median(y.map(function (yy, q) { return yy - b * (x[q] - mid); }));
  return { slope: b * 10, p: Math.min(1, 2 * normSf(Math.abs(z))), tau: s / (0.5 * n * (n - 1)), n: n, mid: mid, mean: mean };
}

/* Benjamini-Hochberg adjusted p-values; entries that are not numbers stay as they are. */
function fdr(p) {
  var idx = [], q = p.slice(), k, m, prev = 1;
  p.forEach(function (x, i) { if (has(x)) idx.push(i); });
  idx.sort(function (a, b) { return p[a] - p[b]; }); m = idx.length;
  for (k = m - 1; k >= 0; k--) { prev = Math.min(prev, p[idx[k]] * m / (k + 1)); q[idx[k]] = prev; }
  return q;
}

var api = { sen: sen, fdr: fdr, MIN_YEARS: MIN_YEARS,
  METHOD: "Sen's slope, modified Mann-Kendall test (Hamed-Rao), Benjamini-Hochberg FDR" };
if (typeof module !== "undefined" && module.exports) module.exports = api; else window.TrendStats = api;
})();
