/* Trend Detective concept. Reads data/meta.json + one JSON per variable
   (format: README.md) and draws the stack, map and analysis views. No build step, no dependencies.
   Trends and significance: Sen's slope, modified Mann-Kendall test and FDR adjustment (stats.js);
   the maps and the sea average come computed from the backend with the same method.
   Code generated with AI assistance. */
(function () {
"use strict";

// Real data from the backend (/api/trends/<set>/, made by backend/exportar_trends.py); "?datos=<set>" picks
// the region and period. "?datos=ficheros" reads the static files in data/ (pipeline/fetch_real_data.py).
var DATA_SET = new URLSearchParams(location.search).get("datos") || "mundo-1982";   // world ocean, first set with sea temperature
var DATA_DIR = DATA_SET === "ficheros" || DATA_SET === "sintetico" ? "data/" : "/api/trends/" + encodeURIComponent(DATA_SET) + "/";
var API_ROOT = (new URLSearchParams(location.search).get("api") || "").replace(/\/$/, "");
var VISIBLE = 4;                 // layers in focus in a stack
var ALPHA = 0.05;                // significance level
var ISO = { ux: 1, uy: 0.10, vx: -0.35, vy: 0.30 };   // screen vectors of one grid step east / south
var STEP_MS = 420, SNAP_MS = 260, POP_MS = 460, MAX_ZOOM = 12;

var ready = false, meta, vars = {}, cellOf, nx, ny, nOcean, weights;
var state = {
  screen: "viz",                 // "viz" | "analysis"
  mode: "time",                  // "time" | "vars" | "trend" | "index" | "pair"
  indexVars: [],                 // variables that enter the combined change index
  pair: [],                      // the two variables of the bivariate map
  varId: null,                   // active variable (time stack, trend map)
  year: null,                    // highlighted year
  shown: [],                     // ticked variable ids (stack by variable, analysis), up to VISIBLE
  offset: 0,                     // first focused year layer in "time" mode
  focus: null,                   // layer opened as a flat map, or null
  sel: { type: "region" },       // region | point {cell} | area {i0,i1,j0,j1,cells}
  baseline: "all",               // time-stack colour reference: whole-sea mean, local mean, or absolute min–max
  corr: "raw",                   // analysis correlation: "raw" | "detrended"
  panelOpen: true
};
var view = { offTime: 0, offVars: 0, focusT: 0, focusPos: 0, zoom: 1, cx: 0, cy: 0 };   // animated / continuous values
var $ = function (id) { return document.getElementById(id); };
var canvas = $("view"), tip = $("tip");
var C = {}, geom = null, imgCache = {}, seriesCache = {}, forecastCache = {}, drag = null, pan = null;
var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ---------------- loading ---------------- */
function getJSON(path) { return fetch(path).then(function (r) { if (!r.ok) throw new Error(path + " " + r.status); return r.json(); }); }
function load() {
  getJSON(DATA_DIR + "meta.json").then(function (m) {
    meta = m; nx = m.region.nx; ny = m.region.ny; nOcean = m.ocean_index.length;
    view.cx = nx / 2; view.cy = ny / 2;
    cellOf = new Int32Array(nx * ny).fill(-1); weights = new Float64Array(nOcean);
    m.ocean_index.forEach(function (flat, k) {
      cellOf[flat] = k;
      weights[k] = Math.cos((m.region.lat_north - (Math.floor(flat / nx) + 0.5) * m.region.cell_deg) * Math.PI / 180);
    });
    return Promise.all(m.variables.map(function (v) {
      return getJSON(DATA_DIR + v.file).then(function (d) {
        var mean = new Float64Array(nOcean), cnt = new Float64Array(nOcean), n = d.values.length, sum = 0, tot = 0, validValues = [], y, k, x;
        for (y = 0; y < n; y++) for (k = 0; k < nOcean; k++) { x = d.values[y][k]; if (x === null) continue; mean[k] += x; cnt[k]++; sum += x; tot++; validValues.push(x); }
        for (k = 0; k < nOcean; k++) mean[k] = cnt[k] ? mean[k] / cnt[k] : NaN;
        validValues.sort(function (a, b) { return a - b; });
        function percentile(p) { var pos = (validValues.length - 1) * p, lo = Math.floor(pos), hi = Math.ceil(pos); return validValues[lo] + (validValues[hi] - validValues[lo]) * (pos - lo); }
        d.cellMean = mean; d.grand = sum / (tot || 1); d.absoluteLow = validValues.length ? percentile(0.02) : NaN; d.absoluteHigh = validValues.length ? percentile(0.98) : NaN;
        var sample = []; for (y = 0; y < n; y++) for (k = y % 5; k < nOcean; k += 5) if (d.values[y][k] !== null) sample.push(Math.abs(d.values[y][k] - d.grand));
        sample.sort(function (a, b) { return a - b; }); d.spatialScale = sample[Math.floor(sample.length * 0.98)] || 1;
        // q = p adjusted for multiple tests (FDR): over the cells for the maps, over the variables for the sea average.
        d.trend.q = d.trend.p_adj || d.trend.p; d.regional.q = has(d.regional.p_adj) ? d.regional.p_adj : d.regional.p;
        d.info = v; vars[v.id] = d;
      });
    }));
  }).then(function () {
    state.varId = meta.variables[0].id;
    state.year = meta.years[meta.years.length - 1];
    state.shown = meta.variables.slice(0, VISIBLE).map(function (v) { return v.id; });
    state.indexVars = meta.variables.map(function (v) { return v.id; });
    state.pair = meta.variables.slice(0, 2).map(function (v) { return v.id; });
    $("loading").hidden = true; ready = true;
    buildSidebar(); render();
  }).catch(function (e) {
    $("loading").textContent = "Could not load the data files. Serve this folder over HTTP (python3 -m http.server) and reload.";
    console.error(e);
    // The default backend set may not exist (e.g. while a source has gaps): open the world set with sea
    // temperature that has most years, else the first one, from the backend's list.
    if (!new URLSearchParams(location.search).get("datos") && DATA_DIR !== "data/") {
      return getJSON(API_ROOT + "/api/trends").then(function (sets) {
        var sst = sets.filter(function (d) { return d.variables.indexOf("sst") >= 0; }).sort(function (a, b) { return a.desde - b.desde; });
        var pick = sst.filter(function (d) { return d.id.indexOf("mundo") === 0; })[0] || sst[0] || sets[0];
        if (!pick) throw e;
        var q = new URLSearchParams(location.search); q.set("datos", pick.id); location.search = q.toString();
      }).catch(function () { $("loading").textContent = "Could not load the data. Please try again later."; });
    }
  });
}

/* ---------------- statistics (correlations; trends are in stats.js) ---------------- */
function has(x) { return x !== null && x !== undefined && isFinite(x); }   // missing years are null in the data
function median(a) { a = a.slice().sort(function (x, y) { return x - y; }); var h = a.length >> 1; return a.length % 2 ? a[h] : (a[h - 1] + a[h]) / 2; }
function detrend(v, slopePerDecade) { var mid = (v.length - 1) / 2; return v.map(function (x, k) { return has(x) ? x - slopePerDecade / 10 * (k - mid) : null; }); }
function pearson(a, b) {                     // over the years both series have
  var ks = [], ma = 0, mb = 0, k, sab = 0, saa = 0, sbb = 0;
  for (k = 0; k < a.length; k++) if (has(a[k]) && has(b[k])) { ks.push(k); ma += a[k]; mb += b[k]; }
  if (ks.length < 3) return NaN;
  ma /= ks.length; mb /= ks.length;
  ks.forEach(function (k) { sab += (a[k] - ma) * (b[k] - mb); saa += (a[k] - ma) * (a[k] - ma); sbb += (b[k] - mb) * (b[k] - mb); });
  return saa && sbb ? sab / Math.sqrt(saa * sbb) : 0;
}

/* ---------------- helpers ---------------- */
function readTheme() {
  var css = getComputedStyle(document.documentElement);
  ["sheet", "paper", "ink", "ink-2", "rule", "probe", "land", "neg", "mid", "pos", "seq"].forEach(function (k) { C[k] = css.getPropertyValue("--" + k).trim(); });
}
function rgb(h) { h = h.replace("#", ""); return [parseInt(h.substr(0, 2), 16), parseInt(h.substr(2, 2), 16), parseInt(h.substr(4, 2), 16)]; }
function diverge(t) {
  t = Math.max(-1, Math.min(1, t));
  var m = rgb(C.mid), p = rgb(t < 0 ? C.neg : C.pos), w = Math.pow(Math.abs(t), 0.8);
  return [m[0] + (p[0] - m[0]) * w, m[1] + (p[1] - m[1]) * w, m[2] + (p[2] - m[2]) * w];
}
function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
function num(v, d, signed) { if (!has(v)) return "–"; return (v < 0 ? "−" : (signed ? "+" : "")) + Math.abs(v).toFixed(d); }
function pText(p) { return p < 0.001 ? "adjusted p < 0.001" : "adjusted p = " + p.toFixed(3); }
function unit(v) { return v.unit ? " " + v.unit : ""; }
function deg(v, pos, neg, d) { return Math.abs(v).toFixed(d === undefined ? 1 : d) + "°" + (v >= 0 ? pos : neg); }
function lonOf(i) { return meta.region.lon_west + i * meta.region.cell_deg; }
function latOf(j) { return meta.region.lat_north - j * meta.region.cell_deg; }
function cellLonLat(flat) { return deg(latOf(Math.floor(flat / nx) + 0.5), "N", "S") + ", " + deg(lonOf(flat % nx + 0.5), "E", "W"); }
function yearIndex(y) { return meta.years.indexOf(y); }
function varIndex(id) { for (var k = 0; k < meta.variables.length; k++) if (meta.variables[k].id === id) return k; return -1; }
function yearLayer(y) { return { key: "y" + y, year: y, label: String(y) }; }
function varLayer(id) { return { key: "v" + id, varId: id, label: vars[id].info.name }; }
function easeInOut(x) { return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; }
function easeOut(x) { return 1 - Math.pow(1 - x, 3); }
function shownSorted() { return state.shown.slice().sort(function (a, b) { return varIndex(a) - varIndex(b); }); }
function shownContiguous() { var s = shownSorted(); for (var k = 1; k < s.length; k++) if (varIndex(s[k]) !== varIndex(s[k - 1]) + 1) return false; return true; }
function flatMode() { return state.mode === "trend" || state.mode === "index" || state.mode === "pair"; }
function multiPick() { return state.screen === "analysis" || state.mode === "vars" || state.mode === "index" || state.mode === "pair"; }
function pickedIds() { return state.screen === "analysis" || state.mode === "vars" ? shownSorted() : state.mode === "index" ? state.indexVars : state.mode === "pair" ? state.pair : [state.varId]; }
function indexLayer() { var ids = meta.variables.map(function (v) { return v.id; }).filter(function (id) { return state.indexVars.indexOf(id) >= 0; }); return { key: "i" + ids.join("+"), index: ids, label: "Combined change index" }; }
function pairLayer() { return { key: "b" + state.pair.join("+"), pair: state.pair, label: vars[state.pair[0]].info.name + " × " + vars[state.pair[1]].info.name }; }
function el(tag, cls, txt) { var e = document.createElement(tag); if (cls) e.className = cls; if (txt !== undefined) e.textContent = txt; return e; }

/* layers currently in the stack: [{layer, pos, w}]  pos 0..3 = in focus, w = colour weight (0 = grey ghost) */
function slots() {
  var out = [], list, off, n, k;
  if (state.mode === "time" || shownContiguous()) {
    if (state.mode === "time") { list = meta.years.slice().reverse().map(yearLayer); off = view.offTime; n = VISIBLE; }
    else { list = meta.variables.map(function (v) { return varLayer(v.id); }); off = view.offVars; n = state.shown.length; }
    for (k = Math.floor(off) - 1; k <= Math.ceil(off) + n; k++) {
      if (k < 0 || k >= list.length) continue;
      var pos = k - off, dist = pos < 0 ? -pos : Math.max(0, pos - (n - 1));
      out.push({ layer: list[k], pos: pos, w: Math.max(0, 1 - dist) });
    }
    return out;
  }
  var s = shownSorted(), first = varIndex(s[0]), last = varIndex(s[s.length - 1]);
  if (first > 0) out.push({ layer: varLayer(meta.variables[first - 1].id), pos: -1, w: 0 });
  s.forEach(function (id, i) { out.push({ layer: varLayer(id), pos: i, w: 1 }); });
  if (last < meta.variables.length - 1) out.push({ layer: varLayer(meta.variables[last + 1].id), pos: s.length, w: 0 });
  return out;
}
function layerValue(layer, k) {             // normalised colour value for ocean cell k
  if (layer.year !== undefined) {
    var d = vars[state.varId], v = d.values[yearIndex(layer.year)][k];
    if (v === null) return NaN;
    if (state.baseline === "absolute") return d.absoluteHigh === d.absoluteLow ? 0 : 2 * (v - d.absoluteLow) / (d.absoluteHigh - d.absoluteLow) - 1;
    return state.baseline === "cell" ? (v - d.cellMean[k]) / d.scale.anomaly : (v - d.grand) / d.spatialScale;
  }
  var dv = vars[layer.varId], sl = dv.trend.slope_per_decade[k]; return sl === null ? NaN : sl / dv.scale.slope;
}
/* Combined change index. For each variable: how far the place has moved over the whole period, in units of
   its own year-to-year variability (|Sen slope| × period ÷ SD of the detrended years); 0 when the trend is
   not significant. The index is the mean over the chosen variables. */
var changeCache = {}, indexCache = {};
function changeOf(id) {                     // per cell, cached
  if (changeCache[id]) return changeCache[id];
  var d = vars[id], n = meta.years.length, mid = (n - 1) / 2, out = new Float64Array(nOcean), k, y, x, b, cnt, m, ss, e;
  for (k = 0; k < nOcean; k++) {
    b = d.trend.slope_per_decade[k]; if (!has(b)) { out[k] = NaN; continue; }
    if (!(d.trend.q[k] < ALPHA)) { out[k] = 0; continue; }
    cnt = 0; m = 0; ss = 0;
    for (y = 0; y < n; y++) { x = d.values[y][k]; if (x === null) continue; e = x - b / 10 * (y - mid); cnt++; m += e; ss += e * e; }
    var sd = cnt > 2 ? Math.sqrt(Math.max(0, ss - m * m / cnt) / (cnt - 1)) : 0;
    out[k] = sd > 0 ? Math.abs(b) / 10 * (n - 1) / sd : 0;
  }
  changeCache[id] = out; return out;
}
function indexOf(layer) {                   // {val: per cell, max: colour ceiling}
  if (indexCache[layer.key]) return indexCache[layer.key];
  var val = new Float64Array(nOcean), parts = layer.index.map(changeOf), k, q, s, c, sorted = [];
  for (k = 0; k < nOcean; k++) { s = 0; c = 0; for (q = 0; q < parts.length; q++) if (!isNaN(parts[q][k])) { s += parts[q][k]; c++; } val[k] = c ? s / c : NaN; if (c) sorted.push(val[k]); }
  sorted.sort(function (a, b) { return a - b; });
  var out = { val: val, max: Math.max(0.5, sorted[Math.floor(sorted.length * 0.98)] || 0.5) };
  indexCache[layer.key] = out; return out;
}
function trendClass(d, k) { var b = d.trend.slope_per_decade[k]; if (!has(b)) return -1; return d.trend.q[k] < ALPHA ? (b > 0 ? 2 : 0) : 1; }
// Two-variable map: Pearson correlation of the two yearly series at each place (-1 opposite, +1 together).
var pairCache = {};
function pairCorr(layer) {
  if (pairCache[layer.key]) return pairCache[layer.key];
  var a = vars[layer.pair[0]].values, b = vars[layer.pair[1]].values, r = new Float64Array(nOcean), k;
  for (k = 0; k < nOcean; k++) r[k] = pearson(a.map(function (row) { return row[k]; }), b.map(function (row) { return row[k]; }));
  return (pairCache[layer.key] = r);
}
function cellColor(layer, k) {              // rgb triple, or null for "no data"
  if (layer.index) { var ix = indexOf(layer), v = ix.val[k]; if (isNaN(v)) return null;
    var m = rgb(C.mid), t = rgb(C.seq), w = Math.pow(clamp(v / ix.max, 0, 1), 0.8); return [m[0] + (t[0] - m[0]) * w, m[1] + (t[1] - m[1]) * w, m[2] + (t[2] - m[2]) * w]; }
  if (layer.pair) { var r = pairCorr(layer)[k]; return isNaN(r) ? null : diverge(r); }
  var lv = layerValue(layer, k); return isNaN(lv) ? null : diverge(lv);
}
function layerImage(layer, grey) {          // 1 pixel per grid cell, cached; grey = ghost version
  var key = layer.key + "|" + (layer.year !== undefined ? state.varId + state.baseline : "") + "|" + C.mid + (grey ? "|g" : "");
  if (imgCache[key]) return imgCache[key];
  var cv = document.createElement("canvas"); cv.width = nx; cv.height = ny;
  var g = cv.getContext("2d"), im = g.createImageData(nx, ny), land = rgb(C.land), nodata = rgb(C.rule);
  for (var f = 0; f < nx * ny; f++) {
    var k = cellOf[f], c = k < 0 ? land : (cellColor(layer, k) || nodata), o = f * 4;
    if (grey) { var l = 0.3 * c[0] + 0.59 * c[1] + 0.11 * c[2]; c = [l, l, l]; }
    im.data[o] = c[0]; im.data[o + 1] = c[1]; im.data[o + 2] = c[2]; im.data[o + 3] = k < 0 ? 150 : 255;
  }
  g.putImageData(im, 0, 0); imgCache[key] = cv; return cv;
}
function fitCanvas(cv) {
  var d = window.devicePixelRatio || 1, w = cv.clientWidth, h = cv.clientHeight;
  if (cv.width !== Math.round(w * d) || cv.height !== Math.round(h * d)) { cv.width = Math.round(w * d); cv.height = Math.round(h * d); }
  var g = cv.getContext("2d"); g.setTransform(d, 0, 0, d, 0, 0); g.clearRect(0, 0, w, h);
  return { g: g, w: w, h: h };
}
function text(g, s, x, y, o) {
  o = o || {}; g.font = (o.weight || "400") + " " + (o.size || 11) + "px 'IBM Plex Mono',monospace";
  g.fillStyle = o.color || C["ink-2"]; g.textAlign = o.align || "left"; g.textBaseline = o.base || "middle"; g.fillText(s, x, y);
}

/* ---------------- selection and series ---------------- */
function selKey() { var s = state.sel; return s.type === "point" ? "p" + s.cell : s.type === "area" ? "a" + [s.i0, s.i1, s.j0, s.j1].join("_") : "r"; }
function areaMean(d, cells) {
  return d.values.map(function (row) {
    var a = 0, wsum = 0; for (var q = 0; q < cells.length; q++) { var x = row[cells[q]]; if (x !== null) { a += x * weights[cells[q]]; wsum += weights[cells[q]]; } }
    return wsum ? a / wsum : null;
  });
}
function seriesFor(varId) {                 // {v: yearly values, slope per decade, p: adjusted p}
  var d = vars[varId], s = state.sel, key = varId + "|" + selKey();
  if (seriesCache[key]) return seriesCache[key];
  var out;
  if (s.type === "point") out = { v: d.values.map(function (row) { return row[s.cell]; }), slope: d.trend.slope_per_decade[s.cell], p: d.trend.q[s.cell] };
  else if (s.type === "area") {             // computed here, every variable at once: the adjustment is over the variables
    var fits = meta.variables.map(function (v) { var series = areaMean(vars[v.id], s.cells), f = TrendStats.sen(series); return { v: series, slope: f.slope, p: f.p }; });
    var q = TrendStats.fdr(fits.map(function (f) { return f.p; }));
    meta.variables.forEach(function (v, i) { fits[i].p = q[i]; if (!has(fits[i].slope)) fits[i].slope = NaN; seriesCache[v.id + "|" + selKey()] = fits[i]; });
    return seriesCache[key];
  } else out = { v: d.regional.mean, slope: d.regional.slope_per_decade, p: d.regional.q };
  if (!has(out.slope)) out.slope = NaN;      // too few years for a trend: never "significant"
  if (!has(out.p)) out.p = 1;
  seriesCache[key] = out; return out;
}
function selText() {
  var s = state.sel;
  if (s.type === "point") return "Point at " + cellLonLat(meta.ocean_index[s.cell]);
  if (s.type === "area") return "Area " + deg(latOf(s.j1 + 1), "N", "S") + " to " + deg(latOf(s.j0), "N", "S") + ", " + deg(lonOf(s.i0), "E", "W") + " to " + deg(lonOf(s.i1 + 1), "E", "W") + " (average of " + s.cells.length + " cells)";
  return "Average over the whole " + meta.region.name;
}
function selShort() { return state.sel.type === "point" ? "at point" : state.sel.type === "area" ? "area average" : "sea average"; }
function direction(s, d) {                   // plain word for the table: negligible slopes read as "No change"
  if (!has(s.slope)) return "Too few years";
  if (Math.abs(s.slope) < 0.1 * d.scale.slope) return "No change";
  return s.slope > 0 ? "↑ Higher" : "↓ Lower";
}
function rate(s, info) { return num(s.slope, info.decimals + 1, true) + unit(info) + " per decade"; }

/* ---------------- geometry ---------------- */
function stackGeom(w, h) {
  var pad = 14, gap = h / 4.9, unitW = nx * ISO.ux - ny * ISO.vx;
  var s = Math.max(1, Math.min((w - 2 * pad) / unitW, (gap - 46) / (ny * ISO.vy)));
  return { s: s, gap: gap, ox: (w - unitW * s) / 2 - ny * ISO.vx * s, top: 0.5 * gap - 4 };
}
function isoMatrix(G, pos) { return [ISO.ux * G.s, ISO.uy * G.s, ISO.vx * G.s, ISO.vy * G.s, G.ox, G.top + pos * G.gap]; }
function flatBase(w, h) { return Math.min((w - 80) / nx, (h - 80) / ny); }
function flatMatrix(w, h) { var sc = flatBase(w, h) * view.zoom; return [sc, 0, 0, sc, w / 2 - view.cx * sc, h / 2 - view.cy * sc]; }
function clampView() {
  var hx = nx / (2 * view.zoom), hy = ny / (2 * view.zoom);
  view.cx = clamp(view.cx, hx, nx - hx); view.cy = clamp(view.cy, hy, ny - hy);
}
function resetZoom() { view.zoom = 1; view.cx = nx / 2; view.cy = ny / 2; }
function apply(M, i, j) { return [M[4] + i * M[0] + j * M[2], M[5] + i * M[1] + j * M[3]]; }
function invert(M, px, py) { var det = M[0] * M[3] - M[2] * M[1], dx = px - M[4], dy = py - M[5]; return [(dx * M[3] - M[2] * dy) / det, (M[0] * dy - M[1] * dx) / det]; }
function quad(g, M, i0, j0, i1, j1) {
  var a = apply(M, i0, j0), b = apply(M, i1, j0), c = apply(M, i1, j1), d = apply(M, i0, j1);
  g.beginPath(); g.moveTo(a[0], a[1]); g.lineTo(b[0], b[1]); g.lineTo(c[0], c[1]); g.lineTo(d[0], d[1]); g.closePath();
}
function blit(g, M, img, alpha) {
  if (alpha <= 0.01) return;
  g.save(); g.globalAlpha = alpha; g.transform(M[0], M[1], M[2], M[3], M[4], M[5]); g.imageSmoothingEnabled = false; g.drawImage(img, 0, 0); g.restore();
}
var coastPath = null;                      // sea/land cell edges in grid units, built once
function coast(g, M, width) {              // line around the sea, so pale "no change" cells do not read as land
  var f, i, j, p;
  if (!coastPath) {
    coastPath = new Path2D();
    for (f = 0; f < nx * ny; f++) {
      if (cellOf[f] < 0) continue; i = f % nx; j = Math.floor(f / nx);
      if (i > 0 && cellOf[f - 1] < 0) { coastPath.moveTo(i, j); coastPath.lineTo(i, j + 1); }
      if (i < nx - 1 && cellOf[f + 1] < 0) { coastPath.moveTo(i + 1, j); coastPath.lineTo(i + 1, j + 1); }
      if (j > 0 && cellOf[f - nx] < 0) { coastPath.moveTo(i, j); coastPath.lineTo(i + 1, j); }
      if (j < ny - 1 && cellOf[f + nx] < 0) { coastPath.moveTo(i, j + 1); coastPath.lineTo(i + 1, j + 1); }
    }
  }
  p = new Path2D(); p.addPath(coastPath, new DOMMatrix(M));   // transform the path, not the pen: constant line width
  g.strokeStyle = C["ink-2"]; g.lineWidth = width; g.stroke(p);
}
function drawSelection(g, M, alpha) {
  var s = state.sel; g.save(); g.globalAlpha = alpha;
  if (s.type === "point") { var f = meta.ocean_index[s.cell], p = apply(M, f % nx + 0.5, Math.floor(f / nx) + 0.5);
    g.beginPath(); g.arc(p[0], p[1], 5, 0, 7); g.fillStyle = C.probe; g.fill(); g.strokeStyle = C.sheet; g.lineWidth = 2; g.stroke(); }
  if (s.type === "area") { quad(g, M, s.i0, s.j0, s.i1 + 1, s.j1 + 1); g.strokeStyle = C.sheet; g.lineWidth = 4; g.stroke(); g.strokeStyle = C.probe; g.lineWidth = 2; g.stroke(); }
  g.restore();
}

/* ---------------- main view ---------------- */
function isSelected(L) { return (L.year !== undefined && L.year === state.year) || (L.varId !== undefined && L.varId === state.varId); }
function flatLayer() { return state.mode === "trend" ? varLayer(state.varId) : state.mode === "index" ? indexLayer() : state.mode === "pair" ? pairLayer() : state.focus; }
function isFlat() { return flatMode() || (state.focus && view.focusT >= 1); }
function drawMain() {
  if (!ready || state.screen !== "viz") return;
  var f = fitCanvas(canvas), g = f.g; if (f.w < 50 || f.h < 50) return;
  var FM = flatMatrix(f.w, f.h);
  canvas.classList.toggle("flat", !!isFlat());
  if (flatMode()) { geom = { flat: FM }; drawFlat(g, FM, flatLayer(), f.w, f.h); return; }
  var G = stackGeom(f.w, f.h), list = slots(), t = easeInOut(view.focusT), fade = 1 - t;
  geom = { G: G, slots: list, flat: view.focusT >= 1 ? FM : null };
  list.slice().sort(function (a, b) { return b.pos - a.pos; }).forEach(function (sl) {
    if (state.focus && sl.layer.key === state.focus.key && view.focusT > 0) return;
    var M = isoMatrix(G, sl.pos), sel = isSelected(sl.layer) && sl.w > 0.5;
    blit(g, M, layerImage(sl.layer, true), fade * (1 - sl.w) * 0.6);
    blit(g, M, layerImage(sl.layer, false), fade * sl.w);
    g.save(); g.globalAlpha = fade * (0.35 + 0.65 * sl.w);
    coast(g, M, 0.6);
    quad(g, M, 0, 0, nx, ny); g.strokeStyle = sel ? C.ink : C["ink-2"]; g.lineWidth = sel ? 2 : 0.8; g.stroke();
    var left = apply(M, 0, ny), right = apply(M, nx, 0);          // name under the left corner, reading above the right corner
    text(g, sl.layer.label, left[0], left[1] + 30, { color: C.ink, weight: sel ? "500" : "400", size: 13 });
    if (sl.w > 0.5) text(g, layerInfo(sl.layer), right[0], right[1] - 30, { align: "right", size: 11.5 });
    g.restore();
    if (sl.w > 0.5) drawSelection(g, M, fade);
  });
  if (state.focus && view.focusT > 0) {
    if (view.focusT >= 1) drawFlat(g, FM, state.focus, f.w, f.h);
    else { var A = isoMatrix(G, view.focusPos), M2 = A.map(function (a, k) { return a + (FM[k] - a) * t; });
      blit(g, M2, layerImage(state.focus, false), 1); coast(g, M2, 0.8); quad(g, M2, 0, 0, nx, ny); g.strokeStyle = C.ink; g.lineWidth = 1.5; g.stroke(); }
  }
}
function layerInfo(L) {                      // reading for the current selection, shown at the right of each layer
  if (L.year !== undefined) { var d = vars[state.varId]; return num(seriesFor(state.varId).v[yearIndex(L.year)], d.info.decimals) + unit(d.info) + " " + selShort(); }
  var s = seriesFor(L.varId); return s.p < ALPHA ? (s.slope > 0 ? "↑ rising" : "↓ falling") : "no significant trend";
}
function hatchPattern(g) {
  var cv = document.createElement("canvas"); cv.width = cv.height = 6; var h = cv.getContext("2d");
  h.strokeStyle = C.ink; h.globalAlpha = 0.6; h.lineWidth = 1; h.beginPath(); h.moveTo(-1, 7); h.lineTo(7, -1); h.stroke();
  return g.createPattern(cv, "repeat");
}
function drawFlat(g, M, layer, w, h) {       // front-facing map, clipped to a frame so it can be zoomed
  var sc = M[0], x0 = M[4], y0 = M[5], k, r = meta.region;
  var fx = Math.max(40, x0), fy = Math.max(40, y0), fw = Math.min(w - 40, x0 + nx * sc) - fx, fh = Math.min(h - 40, y0 + ny * sc) - fy;
  g.save(); g.beginPath(); g.rect(fx, fy, fw, fh); g.clip();
  blit(g, M, layerImage(layer, false), 1);
  if (layer.varId !== undefined) {
    var d = vars[layer.varId]; g.fillStyle = hatchPattern(g);
    for (k = 0; k < nOcean; k++) if (!has(d.trend.q[k]) || d.trend.q[k] >= ALPHA) { var fl = meta.ocean_index[k], cx = x0 + (fl % nx) * sc, cy = y0 + Math.floor(fl / nx) * sc;
      if (cx + sc < fx || cx > fx + fw || cy + sc < fy || cy > fy + fh) continue; g.fillRect(cx, cy, sc + 0.5, sc + 0.5); }
  }
  coast(g, M, 0.8);
  drawSelection(g, M, 1);
  if (drag && drag.moved) { var a = drag.a, b = drag.b; quad(g, M, Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]) + 1, Math.max(a[1], b[1]) + 1);
    g.fillStyle = C.probe; g.globalAlpha = 0.18; g.fill(); g.globalAlpha = 1; g.strokeStyle = C.probe; g.lineWidth = 1.5; g.setLineDash([4, 3]); g.stroke(); g.setLineDash([]); }
  g.restore();
  g.strokeStyle = C["ink-2"]; g.lineWidth = 0.8; g.strokeRect(fx, fy, fw, fh);
  var z = view.zoom, pxPerDeg = sc / r.cell_deg, lon, lat, px, py;   // label step: the smallest nice step that leaves room between labels
  function stepFor(gap) { return [1, 2, 5, 10, 15, 30, 60].filter(function (s) { return s * pxPerDeg >= gap; })[0] || 90; }
  var lonStep = stepFor(45), latStep = stepFor(22);
  for (lon = Math.ceil((r.lon_west + (fx - x0) / sc * r.cell_deg) / lonStep) * lonStep; ; lon += lonStep) { px = x0 + (lon - r.lon_west) / r.cell_deg * sc; if (px > fx + fw + 0.5) break;
    text(g, deg(lon, "E", "W", 0), px, fy + fh + 12, { align: "center", size: 10 }); }
  for (lat = Math.floor((r.lat_north - (fy - y0) / sc * r.cell_deg) / latStep) * latStep; ; lat -= latStep) { py = y0 + (r.lat_north - lat) / r.cell_deg * sc; if (py > fy + fh + 0.5) break;
    text(g, deg(lat, "N", "S", 0), fx - 6, py, { align: "right", size: 10 }); }
  text(g, layer.label + (layer.year !== undefined ? " · " + vars[state.varId].info.name : layer.index ? " · " + layer.index.length + " variables" : layer.pair ? " · correlation at each place" : " · trend per decade"), fx, fy - 14, { color: C.ink, weight: "500", size: 13 });
  if (z > 1.01) text(g, "zoom ×" + z.toFixed(1) + " · hold Shift and drag to move", fx + fw, fy - 14, { align: "right", size: 10.5 });
}
function hit(px, py) {                       // -> {layer, slot?, i, j, flat, cell} or null
  if (!geom) return null; var ij, i, j;
  if (geom.flat) { ij = invert(geom.flat, px, py); i = Math.floor(ij[0]); j = Math.floor(ij[1]);
    if (i < 0 || i >= nx || j < 0 || j >= ny) return null; return { layer: flatLayer(), i: i, j: j, flat: j * nx + i, cell: cellOf[j * nx + i] }; }
  if (!geom.slots || view.focusT > 0) return null;
  var order = geom.slots.slice().sort(function (a, b) { return a.pos - b.pos; });
  for (var k = 0; k < order.length; k++) { ij = invert(isoMatrix(geom.G, order[k].pos), px, py); i = Math.floor(ij[0]); j = Math.floor(ij[1]);
    if (i >= 0 && i < nx && j >= 0 && j < ny) return { layer: order[k].layer, slot: order[k], i: i, j: j, flat: j * nx + i, cell: cellOf[j * nx + i] }; }
  var best = null, bestD = Infinity;         // between two layers: take the nearest one
  order.forEach(function (sl) { var q = invert(isoMatrix(geom.G, sl.pos), px, py); if (q[0] < 0 || q[0] >= nx) return;
    var dist = q[1] < 0 ? -q[1] : q[1] - ny; if (dist < bestD) { bestD = dist; best = sl; } });
  return best ? { layer: best.layer, slot: best, i: -1, j: -1, flat: -1, cell: -1 } : null;
}

/* ---------------- animation ---------------- */
var tw = {}, raf = 0;
function tween(key, to, dur, fn, done) {
  if (reduced || !dur) { view[key] = to; delete tw[key]; drawMain(); if (done) done(); return; }
  tw[key] = { from: view[key], to: to, t0: performance.now(), dur: dur, fn: fn, done: done };
  if (!raf) raf = requestAnimationFrame(tick);
}
function tick(now) {
  raf = 0; var finished = [];
  Object.keys(tw).forEach(function (key) { var a = tw[key], x = Math.min(1, (now - a.t0) / a.dur);
    view[key] = a.from + (a.to - a.from) * (a.fn ? a.fn(x) : x); if (x >= 1) { delete tw[key]; finished.push(a); } });
  drawMain(); finished.forEach(function (a) { if (a.done) a.done(); });
  if (Object.keys(tw).length && !raf) raf = requestAnimationFrame(tick);
}

/* ---------------- graphs ---------------- */
function drawSeries(cv, s, info, yi, compact, forecast) {
  var f = fitCanvas(cv), g = f.g, yrs = meta.years, n = yrs.length; if (f.w < 60) return;
  var L = 50, R = 8, T = compact ? 6 : 12, B = compact ? 16 : 24;
  var pv = s.v.filter(has); if (forecast) pv = pv.concat(forecast.lower, forecast.upper, forecast.median);
  var lo = pv.length ? Math.min.apply(null, pv) : 0, hi = pv.length ? Math.max.apply(null, pv) : 1, padY = (hi - lo) * 0.12 || 1; lo -= padY; hi += padY;
  var firstYear = yrs[0], lastYear = forecast ? forecast.years[forecast.years.length - 1] : yrs[n - 1];
  var XYear = function (year) { return L + (f.w - L - R) * (year - firstYear) / (lastYear - firstYear || 1); };
  var X = function (k) { return forecast ? XYear(yrs[k]) : L + (f.w - L - R) * k / (n - 1); };
  var Y = function (v) { return T + (f.h - T - B) * (hi - v) / (hi - lo); };
  var rows = compact ? 2 : 3;
  for (var q = 0; q <= rows; q++) { var val = lo + (hi - lo) * q / rows; g.beginPath(); g.moveTo(L, Y(val)); g.lineTo(f.w - R, Y(val)); g.strokeStyle = C.rule; g.lineWidth = 0.8; g.stroke(); text(g, num(val, Math.min(info.decimals, 3)), L - 6, Y(val), { align: "right", size: 10 }); }
  if (forecast) [firstYear, forecast.last_observed_year, lastYear].forEach(function (year, k) { text(g, String(year), XYear(year), f.h - 7, { align: k === 0 ? "left" : (k === 2 ? "right" : "center"), size: 10 }); });
  else [0, Math.floor((n - 1) / 2), n - 1].forEach(function (k) { text(g, String(yrs[k]), X(k), f.h - 7, { align: k === 0 ? "left" : (k === n - 1 ? "right" : "center"), size: 10 }); });
  if (yi >= 0) { g.beginPath(); g.moveTo(X(yi), T); g.lineTo(X(yi), f.h - B); g.strokeStyle = C["ink-2"]; g.lineWidth = 1; g.setLineDash([2, 3]); g.stroke(); g.setLineDash([]); }
  g.beginPath(); s.v.forEach(function (v, k) { if (!has(v)) return; if (k && has(s.v[k - 1])) g.lineTo(X(k), Y(v)); else g.moveTo(X(k), Y(v)); }); g.strokeStyle = C.ink; g.lineWidth = compact ? 1.5 : 2; g.lineJoin = "round"; g.stroke();
  s.v.forEach(function (v, k) { if (!has(v)) return; g.beginPath(); g.arc(X(k), Y(v), k === yi ? 5 : (compact ? 1.8 : 2.5), 0, 7); g.fillStyle = C.ink; g.fill(); if (k === yi) { g.strokeStyle = C.sheet; g.lineWidth = 2; g.stroke(); } });
  var perYear = s.slope / 10, mid = (n - 1) / 2, rest = [];
  s.v.forEach(function (v, k) { if (has(v)) rest.push(v - perYear * (k - mid)); });
  if (has(perYear) && rest.length) {
    var fit = { mid: mid, mean: median(rest) };      // Sen line: the slope, through the median of the points
    g.beginPath(); g.moveTo(X(0), Y(fit.mean - perYear * fit.mid)); g.lineTo(X(n - 1), Y(fit.mean + perYear * (n - 1 - fit.mid))); g.strokeStyle = C.probe; g.lineWidth = 2; g.setLineDash([6, 4]); g.stroke(); g.setLineDash([]);
  }
  if (forecast) {
    g.beginPath(); forecast.years.forEach(function (year, i) { if (!i) g.moveTo(XYear(year), Y(forecast.upper[i])); else g.lineTo(XYear(year), Y(forecast.upper[i])); });
    for (var j = forecast.years.length - 1; j >= 0; j--) g.lineTo(XYear(forecast.years[j]), Y(forecast.lower[j]));
    g.closePath(); g.globalAlpha = 0.16; g.fillStyle = C.probe; g.fill(); g.globalAlpha = 1;
    var lastIndex = -1; for (var h = s.v.length - 1; h >= 0; h--) if (has(s.v[h])) { lastIndex = h; break; }
    g.beginPath(); if (lastIndex >= 0) g.moveTo(X(lastIndex), Y(s.v[lastIndex]));
    forecast.years.forEach(function (year, i) { if (lastIndex < 0 && !i) g.moveTo(XYear(year), Y(forecast.median[i])); else g.lineTo(XYear(year), Y(forecast.median[i])); });
    g.strokeStyle = C.probe; g.lineWidth = 2; g.setLineDash([3, 3]); g.stroke(); g.setLineDash([]);
    g.beginPath(); g.moveTo(XYear(forecast.years[0]), T); g.lineTo(XYear(forecast.years[0]), f.h - B); g.strokeStyle = C.probe; g.lineWidth = 1; g.setLineDash([2, 3]); g.stroke(); g.setLineDash([]);
  }
}
function forecastFor(id, s) {
  var key = id + "|" + JSON.stringify(s.v);
  if (!forecastCache[key]) forecastCache[key] = fetch(API_ROOT + "/api/forecast", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ years: meta.years, values: s.v }) })
    .then(function (r) { return r.json().then(function (body) { if (!r.ok) throw new Error(body.detail || "Forecast unavailable"); return body; }); });
  return forecastCache[key];
}
function miniGraph(parent, id, detail) {     // title + small graph (+ numbers line when detail is true)
  var d = vars[id], s = seriesFor(id), div = el("div", "mini"), cv = document.createElement("canvas"), note = null;
  div.appendChild(el("h3", "", d.info.name + (d.info.unit ? " (" + d.info.unit + ")" : ""))); div.appendChild(cv);
  if (detail) {
    div.appendChild(el("p", "", (s.slope > 0 ? "↑ " : "↓ ") + rate(s, d.info) + " · " + (s.p < ALPHA ? "significant" : "not significant") + " (" + pText(s.p) + ")"));
    note = el("p", "forecast-note", "Calculating 5-year forecast…"); div.appendChild(note);
  }
  parent.appendChild(div); drawSeries(cv, s, d.info, -1, true);
  if (detail) forecastFor(id, s).then(function (fc) {
    if (!div.isConnected) return;
    drawSeries(cv, s, d.info, -1, true, fc);
    note.textContent = "Damped exponential smoothing · 90% interval · " + fc.years[0] + "–" + fc.years[fc.years.length - 1] + (fc.interpolated_years.length ? " · interpolated " + fc.interpolated_years.join(", ") : "");
  }).catch(function (error) {
    if (!div.isConnected) return;
    note.textContent = error instanceof TypeError || error instanceof SyntaxError ? "Forecast unavailable; connect the Python backend." : (error.message || "Forecast unavailable.");
  });
}

/* Monthly detail of the sea average: the trend of each calendar month (does summer change faster than winter?). */
var MONTHS = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"], MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
function drawMonths(cv, mo, info) {
  var f = fitCanvas(cv), g = f.g; if (f.w < 60) return;
  var L = 50, R = 8, T = 8, B = 18, sl = mo.slope_per_decade, vals = sl.filter(has), lo = Math.min(0, Math.min.apply(null, vals)), hi = Math.max(0, Math.max.apply(null, vals));
  if (hi === lo) hi = lo + 1; var pad = (hi - lo) * 0.1; lo -= lo < 0 ? pad : 0; hi += hi > 0 ? pad : 0;
  var Y = function (v) { return T + (f.h - T - B) * (hi - v) / (hi - lo); }, bw = (f.w - L - R) / 12;
  [lo, hi].concat(lo < 0 && hi > 0 ? [0] : []).forEach(function (v) { g.beginPath(); g.moveTo(L, Y(v)); g.lineTo(f.w - R, Y(v)); g.strokeStyle = v === 0 ? C["ink-2"] : C.rule; g.lineWidth = 0.8; g.stroke(); text(g, num(v, Math.min(info.decimals + 1, 3), true), L - 6, Y(v), { align: "right", size: 10 }); });
  var hatch = hatchPattern(g);
  sl.forEach(function (v, m) {
    text(g, MONTHS[m], L + bw * (m + 0.5), f.h - 7, { align: "center", size: 10 }); if (!has(v)) return;
    var x = L + bw * m + 2, y0 = Y(Math.max(v, 0)), h = Math.max(1, Math.abs(Y(v) - Y(0))), sig = has(mo.p_adj[m]) && mo.p_adj[m] < ALPHA;
    g.fillStyle = v >= 0 ? C.pos : C.neg; g.globalAlpha = sig ? 1 : 0.45; g.fillRect(x, y0, bw - 4, h); g.globalAlpha = 1;
    if (!sig) { g.fillStyle = hatch; g.fillRect(x, y0, bw - 4, h); }
  });
  var a = mo.anomaly, note = "Trend of each calendar month, " + (info.unit ? info.unit + " " : "") + "per decade. Hatched: not significant.";
  if (a && has(a.slope_per_decade)) note += " All " + a.n_months + " months, seasonal cycle removed: " + num(a.slope_per_decade, info.decimals + 1, true) + unit(info) + " per decade (" + (a.p < 0.001 ? "p < 0.001" : "p = " + a.p.toFixed(3)) + ").";
  $("months-note").textContent = note;
  cv.onmousemove = function (e) { var r = cv.getBoundingClientRect(), m = Math.floor((e.clientX - r.left - L) / bw); if (m < 0 || m > 11 || !has(sl[m])) { tip.style.opacity = 0; return; }
    tip.textContent = MONTH_NAMES[m] + ": " + num(sl[m], info.decimals + 1, true) + unit(info) + " per decade, " + (mo.p_adj[m] < ALPHA ? "significant" : "not significant") + " (" + pText(mo.p_adj[m]) + ")";
    tip.style.left = Math.min(e.clientX, window.innerWidth - 400) + "px"; tip.style.top = e.clientY + "px"; tip.style.opacity = 1; };
  cv.onmouseleave = function () { tip.style.opacity = 0; };
}

/* ---------------- right panel (visualisation screen) ---------------- */
function updatePanel() {
  if (!ready) return;
  var analysis = state.screen === "analysis";
  $("viz-panel").hidden = analysis; $("analysis").hidden = !analysis;
  if (analysis) { renderAnalysis(); return; }
  if (!state.panelOpen) return;
  var multi = state.mode === "vars" || state.mode === "pair", info = vars[state.varId].info, yi = state.mode === "time" ? yearIndex(state.year) : -1;
  $("panel-title").textContent = state.mode === "pair" ? "The two variables" : multi ? "Stacked variables" : state.mode === "index" ? "Combined change index" : info.name + (info.unit ? " (" + info.unit + ")" : "");
  $("panel-where").textContent = selText();
  $("clear").hidden = state.sel.type === "region";
  var idx = state.mode === "index";
  $("single").hidden = multi || idx; $("multi").hidden = !multi; $("index-note").hidden = !idx;
  if (multi) { var box = $("multi"); box.textContent = ""; pickedIds().forEach(function (id) { miniGraph(box, id, state.mode === "pair"); }); }
  else if (!idx) {
    var s1 = seriesFor(state.varId);
    $("verdict").textContent = !has(s1.slope) ? "Too few years for a trend" : (s1.p < ALPHA ? (s1.slope > 0 ? "Rising " : "Falling ") + rate(s1, info).replace(/^[+−]/, "") + " · statistically significant" : "No statistically significant trend") + " (" + pText(s1.p) + ")";
    drawSeries($("chart"), s1, info, yi, false);
  }
  var mo = !multi && !idx && state.sel.type === "region" ? vars[state.varId].regional.monthly : null;
  $("monthly").hidden = !mo;
  if (mo) drawMonths($("months"), mo, info);
  var tb = $("allvars"), onScreen = pickedIds(); tb.textContent = "";
  meta.variables.filter(function (v) { return onScreen.indexOf(v.id) >= 0; }).forEach(function (v) {
    var s = seriesFor(v.id), tr = el("tr", (multi || state.mode === "index" ? pickedIds().indexOf(v.id) >= 0 : v.id === state.varId) ? "on" : "");
    tr.appendChild(el("td", "", v.name)); tr.appendChild(el("td", "", direction(s, vars[v.id]))); tr.appendChild(el("td", "", s.p < ALPHA ? "Yes" : "No"));
    tr.addEventListener("click", function () { pickVar(v.id); }); tb.appendChild(tr);
  });
}

/* ---------------- analysis screen ---------------- */
function renderAnalysis() {
  var root = $("analysis"); root.textContent = "";
  var head = el("div", "a-head"); head.appendChild(el("h2", "", "Analysis")); head.appendChild(el("span", "fact dim", selText()));
  if (state.sel.type !== "region") { var c = el("button", "ghost small", "Clear selection"); c.type = "button"; c.addEventListener("click", function () { setSel({ type: "region" }); }); head.appendChild(c); }
  root.appendChild(head);

  var left = el("section"); left.appendChild(el("h2", "", "Time series of the ticked variables"));
  root.appendChild(left); shownSorted().forEach(function (id) { miniGraph(left, id, true); });

  var right = el("section"); root.appendChild(right);
  right.appendChild(el("h2", "", "Trend of every variable"));
  var tbl = el("table", "allvars"), th = el("thead"), hr = el("tr"); ["Variable", "Per decade", "Adjusted p", "Trend", "Signif."].forEach(function (t) { hr.appendChild(el("th", "", t)); });
  th.appendChild(hr); tbl.appendChild(th); var tb = el("tbody");
  meta.variables.forEach(function (v) {
    var s = seriesFor(v.id), tr = el("tr", state.shown.indexOf(v.id) >= 0 ? "on" : "");
    tr.appendChild(el("td", "", v.name)); tr.appendChild(el("td", "num", num(s.slope, v.decimals + 1, true) + unit(v)));
    tr.appendChild(el("td", "num", !has(s.slope) ? "–" : s.p < 0.001 ? "< 0.001" : s.p.toFixed(3))); tr.appendChild(el("td", "", direction(s, vars[v.id]))); tr.appendChild(el("td", "", s.p < ALPHA ? "Yes" : "No"));
    tr.addEventListener("click", function () { pickVar(v.id); }); tb.appendChild(tr);
  });
  tbl.appendChild(tb); var w1 = el("div", "tablewrap"); w1.appendChild(tbl); right.appendChild(w1);
  right.appendChild(el("p", "note", "Per decade: Sen's slope (the median of the slopes between every pair of years). Significance: modified Mann-Kendall test, which allows for one year resembling the next, with the p-values adjusted for testing several " + (state.sel.type === "point" ? "places" : "variables") + " at once (Benjamini-Hochberg). Significant means adjusted p < " + ALPHA + "."));

  var rowh = el("div", "a-row"); rowh.appendChild(el("h2", "", "Correlation between variables")); rowh.firstChild.style.margin = "0";
  var seg = el("span", "seg small"); [["raw", "yearly values"], ["detrended", "trend removed"]].forEach(function (o) {
    var b = el("button", "", o[1]); b.type = "button"; b.setAttribute("aria-pressed", String(state.corr === o[0])); b.addEventListener("click", function () {
      var keep = [root, root.parentNode, document.scrollingElement].map(function (e) { return [e, e.scrollTop]; });
      state.corr = o[0]; renderAnalysis(); keep.forEach(function (k) { k[0].scrollTop = k[1]; });
    }); seg.appendChild(b); });
  rowh.appendChild(seg); right.appendChild(rowh);
  right.appendChild(el("p", "note", "Yearly values: compares the series as they are, so two variables that both rise over time look related. Trend removed: compares only the ups and downs from one year to the next."));
  var series = meta.variables.map(function (v) { var s = seriesFor(v.id); return state.corr === "raw" ? s.v : detrend(s.v, s.slope); });
  var ct = el("table", "corr"), r0 = el("tr"); r0.appendChild(el("th", "", "")); meta.variables.forEach(function (v, k) { r0.appendChild(el("th", "", String(k + 1))); }); ct.appendChild(r0);
  meta.variables.forEach(function (v, a) {
    var tr = el("tr"); tr.appendChild(el("th", "rowh", (a + 1) + "  " + v.name));
    meta.variables.forEach(function (u, b) {
      var r = a === b ? 1 : pearson(series[a], series[b]), td = el("td", a === b ? "self" : "", a === b ? "–" : num(r, 2));
      if (a !== b) { var col = diverge(r * 0.75); td.style.background = "rgb(" + col.map(Math.round).join(",") + ")"; td.title = v.name + " vs " + u.name + ": r = " + num(r, 2); }
      tr.appendChild(td);
    });
    ct.appendChild(tr);
  });
  var w2 = el("div", "tablewrap"); w2.appendChild(ct); right.appendChild(w2);
  right.appendChild(el("p", "note", "Pearson correlation of the " + meta.years.length + " yearly values for the current selection: +1 means the two move together, −1 opposite. Two variables that both trend will correlate even if unrelated; “trend removed” compares only their year-to-year swings."));
}

/* ---------------- sidebar, legend, stack bar ---------------- */
function buildSidebar() {
  $("synthetic-flag").hidden = !meta.synthetic;
  $("region-name").textContent = meta.region.name;
  $("period").textContent = meta.years[0] + " to " + meta.years[meta.years.length - 1] + ", yearly values";
  var ul = $("varlist");
  meta.variables.forEach(function (v) {
    var li = document.createElement("li"), b = document.createElement("button");
    b.type = "button"; b.dataset.id = v.id;
    [["box", ""], ["name", v.name], ["unit", v.unit]].forEach(function (p) { b.appendChild(el("span", p[0], p[1])); });
    b.addEventListener("click", function () { pickVar(v.id); }); li.appendChild(b); ul.appendChild(li);
  });
}
function updateSidebar() {
  var multi = multiPick(), ids = pickedIds();
  $("varlist").classList.toggle("multi", multi);
  $("var-hint").textContent = multi ? (state.screen !== "analysis" && state.mode === "index" ? "Tick the variables that enter the index." : state.screen !== "analysis" && state.mode === "pair" ? "Tick two variables to compare." : "Tick up to " + VISIBLE + " variables to " + (state.screen === "analysis" ? "graph." : "stack and compare.")) : "Choose one variable.";
  document.querySelectorAll("#varlist button").forEach(function (b) { b.setAttribute("aria-pressed", String(ids.indexOf(b.dataset.id) >= 0)); });
  var box = $("definitions"); box.textContent = "";
  ids.forEach(function (id) { var info = vars[id].info, p = el("p"); p.appendChild(el("b", "", info.name + ". ")); p.appendChild(document.createTextNode(info.definition)); p.appendChild(el("span", "src", "Source: " + info.source)); box.appendChild(p); });
}
function updateLegend() {
  var d = vars[state.varId], info = d.info, node = $("legend"), parts = [], tail = [], fl = flatLayer();
  function ramp(a, b) { return "<span>" + a + "<span class=\"ramp\"></span>" + b + "</span>"; }
  if (state.mode === "time") {
    var own = state.baseline === "cell", absolute = state.baseline === "absolute";
    parts.push("<span>Colour reference: <span class=\"seg small\" id=\"baseline\"><button type=\"button\" data-b=\"all\" aria-pressed=\"" + (state.baseline === "all") + "\">whole sea, all years</button><button type=\"button\" data-b=\"cell\" aria-pressed=\"" + own + "\">each place, all years</button><button type=\"button\" data-b=\"absolute\" aria-pressed=\"" + absolute + "\">absolute value</button></span></span>", "<span class=\"br\"></span>");
    tail.push("<span class=\"br\"></span>", absolute ? ramp("≤ " + num(d.absoluteLow, info.decimals) + unit(info), "≥ " + num(d.absoluteHigh, info.decimals) + unit(info)) : ramp("below average", "above"), absolute ? "<span>2nd–98th percentile; endpoint colours clipped</span>" : "<span>same range on every layer: ±" + num(own ? d.scale.anomaly : d.spatialScale, info.decimals) + unit(info) + "</span>");
  } else if (state.mode === "index") {
    var ix = indexOf(indexLayer());
    parts.push("<span>0<span class=\"ramp seq\"></span>≥ " + ix.max.toFixed(1) + "</span>", "<span>significant change over the period ÷ year-to-year variability, mean of " + state.indexVars.length + " variable" + (state.indexVars.length > 1 ? "s" : "") + "</span>");
  } else if (state.mode === "pair") {
    parts.push(ramp("−1 opposite", "+1 together"), "<span>correlation of the yearly values of " + vars[state.pair[0]].info.name + " and " + vars[state.pair[1]].info.name + " at each place</span>");
  } else parts.push(ramp("falling", "rising"), state.mode === "trend" ? "<span>range ±" + d.scale.slope + unit(info) + " per decade</span>" : "<span>one fixed range per variable (units differ)</span>");
  if (fl && fl.varId !== undefined && isFlat()) parts.push("<span><span class=\"sw hatch\"></span>not significant (adjusted p ≥ " + ALPHA + ")</span>");
  if (state.mode !== "time") parts.push("<span class=\"br\"></span>");
  parts.push("<span><span class=\"sw land\"></span>land</span>", "<span><span class=\"sw nodata\"></span>no data</span>", "<span><span class=\"sw probe\"></span>selection</span>");
  node.innerHTML = parts.concat(tail).join("");
  var bl = $("baseline"); if (bl) bl.querySelectorAll("button").forEach(function (b) { b.addEventListener("click", function () { state.baseline = b.dataset.b; render(); }); });
}
function updateStackBar() {
  var trend = flatMode();
  $("back").hidden = !state.focus; $("up").hidden = $("down").hidden = $("scroll-pos").hidden = trend;
  var hint = $("stack-hint"), flatHint = "Click a point or drag a box. Scroll to zoom.";
  if (trend) { hint.textContent = flatHint; return; }
  var nYears = meta.years.length, nVars = meta.variables.length;
  if (state.focus) {
    $("scroll-pos").textContent = state.focus.label;
    var seq = focusSequence(), at = seq.indexOf(state.focus.year !== undefined ? state.focus.year : state.focus.varId);
    $("up").disabled = at <= 0; $("down").disabled = at >= seq.length - 1; hint.textContent = flatHint;
  } else if (state.mode === "time") {
    $("scroll-pos").textContent = meta.years[nYears - VISIBLE - state.offset] + "–" + meta.years[nYears - 1 - state.offset];
    $("up").disabled = state.offset <= 0; $("down").disabled = state.offset >= nYears - VISIBLE;
    hint.textContent = "Scroll to move. Click a map to open it.";
  } else {
    var s = shownSorted(), first = varIndex(s[0]), contiguous = shownContiguous() && s.length === VISIBLE;
    $("scroll-pos").textContent = s.length + " of " + nVars + " shown";
    $("up").disabled = contiguous && first <= 0; $("down").disabled = contiguous && first >= nVars - VISIBLE;
    hint.textContent = "Scroll to move. Click a map to open it.";
  }
}

/* ---------------- state changes ---------------- */
function focusSequence() { return state.mode === "time" ? meta.years.slice().reverse() : shownSorted(); }
function varWindow(start) { return meta.variables.slice(start, start + VISIBLE).map(function (v) { return v.id; }); }
function step(dir) {                          // buttons / arrow keys: one layer down (+1) or up (−1)
  if (flatMode()) return;
  if (state.focus) {
    var seq = focusSequence(), at = seq.indexOf(state.focus.year !== undefined ? state.focus.year : state.focus.varId) + dir;
    if (at < 0 || at >= seq.length) return;
    if (state.mode === "time") { state.year = seq[at]; state.focus = yearLayer(seq[at]); revealYear(); view.offTime = state.offset; }
    else { state.varId = seq[at]; state.focus = varLayer(seq[at]); }
    render(); return;
  }
  if (state.mode === "time") {
    var o = clamp(state.offset + dir, 0, meta.years.length - VISIBLE);
    if (o === state.offset) return; state.offset = o; tween("offTime", o, STEP_MS, easeInOut); renderChrome(); return;
  }
  var s = shownSorted(), n = meta.variables.length, was = shownContiguous() && s.length === VISIBLE, start;
  if (was) start = varIndex(s[0]) + dir; else start = dir > 0 ? varIndex(s[0]) : varIndex(s[s.length - 1]) - VISIBLE + 1;
  start = clamp(start, 0, n - VISIBLE);
  state.shown = varWindow(start);
  if (state.shown.indexOf(state.varId) < 0) state.varId = state.shown[0];
  if (was) tween("offVars", start, STEP_MS, easeInOut); else { view.offVars = start; drawMain(); }
  renderChrome();
}
var snapTimer = 0;
function glide(deltaLayers) {                 // wheel: follow the fingers, then snap to four whole layers
  var time = state.mode === "time", key = time ? "offTime" : "offVars", max = (time ? meta.years.length : meta.variables.length) - VISIBLE;
  if (!time && !(shownContiguous() && state.shown.length === VISIBLE)) { step(deltaLayers > 0 ? 1 : -1); return; }
  delete tw[key]; view[key] = clamp(view[key] + deltaLayers, -0.25, max + 0.25); drawMain();
  clearTimeout(snapTimer); snapTimer = setTimeout(function () {
    var target = clamp(Math.round(view[key]), 0, max);
    if (time) state.offset = target; else { state.shown = varWindow(target); if (state.shown.indexOf(state.varId) < 0) state.varId = state.shown[0]; }
    tween(key, target, SNAP_MS, easeOut); renderChrome();
  }, 140);
}
function zoomAt(px, py, factor) {
  var w = canvas.clientWidth, h = canvas.clientHeight, at = invert(geom.flat, px, py);
  view.zoom = clamp(view.zoom * factor, 1, MAX_ZOOM); var sc = flatBase(w, h) * view.zoom;
  view.cx = at[0] - (px - w / 2) / sc; view.cy = at[1] - (py - h / 2) / sc; clampView(); drawMain();
}
function revealYear() {
  var idx = meta.years.length - 1 - yearIndex(state.year), o = state.offset;
  if (idx < o) o = idx; if (idx >= o + VISIBLE) o = idx - VISIBLE + 1; state.offset = o;
}
function syncVarsOffset() { if (shownContiguous()) { view.offVars = varIndex(shownSorted()[0]); delete tw.offVars; } }
function pickVar(id) {                        // sidebar / table click
  if (state.screen !== "analysis" && state.mode === "index") {
    var ai = state.indexVars.indexOf(id);
    if (ai >= 0) { if (state.indexVars.length > 1) state.indexVars.splice(ai, 1); } else state.indexVars.push(id);
  } else if (state.screen !== "analysis" && state.mode === "pair") {
    if (state.pair.indexOf(id) < 0) state.pair = [state.pair[1], id];      // the oldest of the two leaves
  } else if (multiPick()) {
    var at = state.shown.indexOf(id);
    if (at >= 0) { if (state.shown.length > 1) state.shown.splice(at, 1); }
    else { if (state.shown.length >= VISIBLE) state.shown.shift(); state.shown.push(id); }
    if (state.shown.indexOf(state.varId) < 0) state.varId = state.shown.indexOf(id) >= 0 ? id : state.shown[0];
    if (state.focus && state.focus.varId !== undefined && state.shown.indexOf(state.focus.varId) < 0) { state.focus = null; view.focusT = 0; resetZoom(); }
    syncVarsOffset();
  } else state.varId = id;
  render();
}
function setMode(m) {
  state.mode = m; state.focus = null; view.focusT = 0; delete tw.focusT; resetZoom();
  if (m === "vars" && state.shown.indexOf(state.varId) < 0) { state.shown.shift(); state.shown.push(state.varId); }
  if (m === "vars") syncVarsOffset();
  if (m === "time") { revealYear(); view.offTime = state.offset; }
  render();
}
// Two button groups: Visualisation (stacks, trend map) and Analysis (correlations, change index, two
// variables). The change index and two-variable maps are drawn like the visualisation maps (state.screen
// "viz" with mode "index" | "pair"); only "Correlations" uses the analysis panel (state.screen "analysis").
var tab = "viz", atab = "corr";
function setScreen(s) {
  tab = s;
  if (s === "analysis") { setAnalysis(atab); return; }
  state.screen = "viz"; if (state.mode === "index" || state.mode === "pair") { setScreenChrome(); setMode("time"); return; }
  setScreenChrome(); render();
}
function setAnalysis(a) {
  atab = a; tab = "analysis";
  if (a === "corr") { state.screen = "analysis"; setScreenChrome(); render(); }
  else { state.screen = "viz"; setScreenChrome(); setMode(a); }
}
function setScreenChrome() {
  $("row").classList.toggle("analysis", state.screen === "analysis");
  $("modes").hidden = tab === "analysis"; $("amodes").hidden = tab !== "analysis";
  document.querySelectorAll("#screens button").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.screen === tab)); });
  document.querySelectorAll("#amodes button").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.a === atab)); });
}
function openFocus(slot) {
  var L = slot.layer; state.focus = L; view.focusPos = slot.pos; view.focusT = 0; resetZoom();
  if (L.year !== undefined) state.year = L.year; else state.varId = L.varId;
  if (!state.panelOpen) setPanel(true);
  renderChrome(); tween("focusT", 1, POP_MS, null, render);
}
function closeFocus() {
  if (!state.focus) return; resetZoom();
  var key = state.focus.key, found = slots().filter(function (s) { return s.layer.key === key; })[0];
  view.focusPos = found ? found.pos : 0;
  tween("focusT", 0, POP_MS, null, function () { state.focus = null; render(); });
}
function setSel(sel) { state.sel = sel; render(); }
function setPanel(open) {
  state.panelOpen = open; $("row").classList.toggle("panel-closed", !open);
  var b = $("panel-toggle"); b.innerHTML = open ? "&rsaquo;" : "&lsaquo;"; b.setAttribute("aria-expanded", String(open));
  b.title = open ? "Hide graph panel" : "Show graph panel"; b.setAttribute("aria-label", b.title);
}
function renderChrome() {
  document.querySelectorAll("#modes button").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.mode === state.mode)); });
  updateSidebar(); updateLegend(); updateStackBar(); updatePanel();
}
function render() { if (!ready) return; readTheme(); drawMain(); renderChrome(); }

/* ---------------- events ---------------- */
function pos(e) { var r = canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }
canvas.addEventListener("pointerdown", function (e) {
  if (!isFlat()) return; var p = pos(e);
  if (e.shiftKey) { pan = { px: p, cx: view.cx, cy: view.cy }; canvas.setPointerCapture(e.pointerId); return; }
  var h = hit(p[0], p[1]); if (!h) return;
  drag = { a: [h.i, h.j], b: [h.i, h.j], px: p, moved: false }; canvas.setPointerCapture(e.pointerId);
});
canvas.addEventListener("pointermove", function (e) {
  var p = pos(e);
  if (pan) { var sc = geom.flat[0]; view.cx = pan.cx - (p[0] - pan.px[0]) / sc; view.cy = pan.cy - (p[1] - pan.px[1]) / sc; clampView(); drawMain(); return; }
  if (drag) {
    var ij = invert(geom.flat, p[0], p[1]);
    drag.b = [clamp(Math.floor(ij[0]), 0, nx - 1), clamp(Math.floor(ij[1]), 0, ny - 1)];
    if (Math.abs(p[0] - drag.px[0]) + Math.abs(p[1] - drag.px[1]) > 6) drag.moved = true;
    drawMain(); tip.style.opacity = 0; return;
  }
  var h = hit(p[0], p[1]); if (!h) { tip.style.opacity = 0; return; }
  var s;
  if (h.cell < 0) s = h.layer.label + (h.flat < 0 ? "" : " · land");
  else if (h.layer.year !== undefined) { var d = vars[state.varId]; s = h.layer.label + " · " + cellLonLat(h.flat) + " · " + num(d.values[yearIndex(h.layer.year)][h.cell], d.info.decimals) + unit(d.info); }
  else if (h.layer.index) {
    var iv = indexOf(h.layer).val[h.cell], moving = h.layer.index.filter(function (id) { return changeOf(id)[h.cell] > 0; }).map(function (id) { return vars[id].info.name + (vars[id].trend.slope_per_decade[h.cell] > 0 ? " ↑" : " ↓"); });
    s = cellLonLat(h.flat) + " · change index " + (isNaN(iv) ? "–" : iv.toFixed(2)) + (moving.length ? " · " + moving.slice(0, 3).join(", ") + (moving.length > 3 ? " and " + (moving.length - 3) + " more" : "") : " · nothing changing significantly");
  } else if (h.layer.pair) {
    var rc = pairCorr(h.layer)[h.cell];
    s = cellLonLat(h.flat) + " · correlation " + (isNaN(rc) ? "–" : num(rc, 2)) + " · " + vars[h.layer.pair[0]].info.name + " and " + vars[h.layer.pair[1]].info.name;
  } else { var dv = vars[h.layer.varId]; s = dv.info.name + " · " + cellLonLat(h.flat) + " · " + num(dv.trend.slope_per_decade[h.cell], dv.info.decimals + 1, true) + unit(dv.info) + " per decade, " + (!has(dv.trend.q[h.cell]) ? "too few years for a trend" : dv.trend.q[h.cell] < ALPHA ? "significant" : "not significant"); }
  tip.textContent = s; tip.style.left = Math.min(e.clientX, window.innerWidth - 400) + "px"; tip.style.top = e.clientY + "px"; tip.style.opacity = 1;
});
canvas.addEventListener("pointerup", function (e) {
  if (pan) { pan = null; return; }
  if (drag) {
    var d = drag; drag = null;
    if (!d.moved) { var k = cellOf[d.a[1] * nx + d.a[0]]; if (k >= 0) { setSel({ type: "point", cell: k }); return; } drawMain(); return; }
    var i0 = Math.min(d.a[0], d.b[0]), i1 = Math.max(d.a[0], d.b[0]), j0 = Math.min(d.a[1], d.b[1]), j1 = Math.max(d.a[1], d.b[1]), cells = [];
    for (var j = j0; j <= j1; j++) for (var i = i0; i <= i1; i++) { var c = cellOf[j * nx + i]; if (c >= 0) cells.push(c); }
    if (cells.length) setSel({ type: "area", i0: i0, i1: i1, j0: j0, j1: j1, cells: cells }); else drawMain();
    return;
  }
  if (state.focus && view.focusT >= 1 && geom && geom.flat) {    // an opened layer: a click outside the map goes back to the stack
    var q = pos(e), sc = geom.flat[0], x0 = geom.flat[4], y0 = geom.flat[5], w = canvas.clientWidth, hh = canvas.clientHeight;
    if (q[0] < Math.max(40, x0) || q[0] > Math.min(w - 40, x0 + nx * sc) || q[1] < Math.max(40, y0) || q[1] > Math.min(hh - 40, y0 + ny * sc)) closeFocus();
    return;
  }
  if (isFlat() || view.focusT > 0) return;
  var p = pos(e), h = hit(p[0], p[1]); if (!h) return;
  if (h.slot.w > 0.5) openFocus(h.slot); else step(h.slot.pos < 0 ? -1 : 1);
});
canvas.addEventListener("pointerleave", function () { tip.style.opacity = 0; });
canvas.addEventListener("wheel", function (e) {
  if (!geom) return; e.preventDefault();
  var dy = e.deltaMode === 1 ? e.deltaY * 32 : e.deltaY;
  if (isFlat()) { var p = pos(e); zoomAt(p[0], p[1], Math.exp(-dy * 0.0018)); return; }
  if (view.focusT > 0 || !geom.G) return;
  glide(dy / geom.G.gap);
}, { passive: false });
document.addEventListener("keydown", function (e) {
  if (e.key === "Escape" && state.focus) closeFocus();
  if (document.activeElement !== canvas) return;
  if (e.key === "ArrowDown") { step(1); e.preventDefault(); } if (e.key === "ArrowUp") { step(-1); e.preventDefault(); }
});
$("up").addEventListener("click", function () { step(-1); });
$("down").addEventListener("click", function () { step(1); });
$("back").addEventListener("click", closeFocus);
$("clear").addEventListener("click", function () { setSel({ type: "region" }); });
document.querySelectorAll("#modes button").forEach(function (b) { b.addEventListener("click", function () { setMode(b.dataset.mode); }); });
document.querySelectorAll("#screens button").forEach(function (b) { b.addEventListener("click", function () { setScreen(b.dataset.screen); }); });
document.querySelectorAll("#amodes button").forEach(function (b) { b.addEventListener("click", function () { setAnalysis(b.dataset.a); }); });
$("panel-toggle").addEventListener("click", function () { setPanel(!state.panelOpen); render(); });
var panelRaf = 0;
if (window.ResizeObserver) {
  new ResizeObserver(function () { if (state.screen === "viz") { readTheme(); drawMain(); } }).observe($("stage"));
  new ResizeObserver(function () { if (!panelRaf) panelRaf = requestAnimationFrame(function () { panelRaf = 0; if (ready) { readTheme(); updatePanel(); } }); }).observe($("panel"));
}
if (window.matchMedia) window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", render);
new MutationObserver(render).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
if (document.fonts && document.fonts.ready) document.fonts.ready.then(render);
window.TrendDetective = { state: state, view: view, seriesFor: seriesFor, setMode: setMode, setSel: setSel, setScreen: setScreen };   // for debugging in the console
load();
})();
