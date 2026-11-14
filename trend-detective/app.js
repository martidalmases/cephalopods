/* Trend Detective concept. Reads data/meta.json + one JSON per variable
   (format: README.md) and draws the stack and map views. No build step, no dependencies.
   Code generated with AI assistance. */
(function () {
"use strict";

var DATA_DIR = "data/";
var VISIBLE = 4;                 // layers in focus in a stack
var ALPHA = 0.05;                // significance level
var ISO = { ux: 1, uy: 0.10, vx: -0.35, vy: 0.30 };   // screen vectors of one grid step east / south
var MOVE_MS = 320, POP_MS = 460;

var meta, vars = {}, cellOf, nx, ny, nOcean, weights;
var state = {
  mode: "time",                  // "time" | "vars" | "trend"
  varId: null,                   // active variable (time stack, trend map, definition)
  year: null,                    // highlighted year
  shown: [],                     // variable ids stacked in "vars" mode (up to VISIBLE)
  offset: 0,                     // first focused year layer in "time" mode
  focus: null,                   // layer opened as a flat map, or null
  sel: { type: "region" },       // region | point {cell} | area {i0,i1,j0,j1,cells}
  baseline: "all",              // time-stack colours: "cell" (each place's own average) | "all" (whole-sea average)
  panelOpen: true
};
var view = { offTime: 0, offVars: 0, focusT: 0, focusPos: 0 };   // animated values
var $ = function (id) { return document.getElementById(id); };
var canvas = $("view"), tip = $("tip");
var C = {}, geom = null, imgCache = {}, seriesCache = {}, drag = null;
var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ---------------- loading ---------------- */
function getJSON(path) { return fetch(path).then(function (r) { if (!r.ok) throw new Error(path + " " + r.status); return r.json(); }); }
function load() {
  getJSON(DATA_DIR + "meta.json").then(function (m) {
    meta = m; nx = m.region.nx; ny = m.region.ny; nOcean = m.ocean_index.length;
    cellOf = new Int32Array(nx * ny).fill(-1); weights = new Float64Array(nOcean);
    m.ocean_index.forEach(function (flat, k) {
      cellOf[flat] = k;
      weights[k] = Math.cos((m.region.lat_north - (Math.floor(flat / nx) + 0.5) * m.region.cell_deg) * Math.PI / 180);
    });
    return Promise.all(m.variables.map(function (v) {
      return getJSON(DATA_DIR + v.file).then(function (d) {
        var mean = new Float64Array(nOcean), n = d.values.length, sum = 0, y, k;
        for (y = 0; y < n; y++) for (k = 0; k < nOcean; k++) { mean[k] += d.values[y][k] / n; sum += d.values[y][k]; }
        d.cellMean = mean; d.grand = sum / (n * nOcean);
        var sample = []; for (y = 0; y < n; y++) for (k = y % 5; k < nOcean; k += 5) sample.push(Math.abs(d.values[y][k] - d.grand));
        sample.sort(function (a, b) { return a - b; }); d.spatialScale = sample[Math.floor(sample.length * 0.98)] || 1;
        d.info = v; vars[v.id] = d;
      });
    }));
  }).then(function () {
    state.varId = meta.variables[0].id;
    state.year = meta.years[meta.years.length - 1];
    state.shown = meta.variables.slice(0, VISIBLE).map(function (v) { return v.id; });
    $("loading").hidden = true;
    buildSidebar(); render();
  }).catch(function (e) {
    $("loading").textContent = "Could not load the data files. Serve this folder over HTTP (python3 -m http.server) and reload.";
    console.error(e);
  });
}

/* ---------------- statistics (used for area selections) ---------------- */
function lgamma(x) {
  var c = [76.18009172947146, -86.50532032941677, 24.01409824083091, -1.231739572450155, 0.1208650973866179e-2, -0.5395239384953e-5];
  var y = x, t = x + 5.5, s = 1.000000000190015; t -= (x + 0.5) * Math.log(t);
  for (var j = 0; j < 6; j++) s += c[j] / ++y;
  return -t + Math.log(2.5066282746310005 * s / x);
}
function betacf(a, b, x) {
  var qab = a + b, qap = a + 1, qam = a - 1, c = 1, d = 1 - qab * x / qap, h, m, m2, aa, del;
  if (Math.abs(d) < 1e-30) d = 1e-30; d = 1 / d; h = d;
  for (m = 1; m <= 200; m++) {
    m2 = 2 * m; aa = m * (b - m) * x / ((qam + m2) * (a + m2));
    d = 1 + aa * d; if (Math.abs(d) < 1e-30) d = 1e-30; c = 1 + aa / c; if (Math.abs(c) < 1e-30) c = 1e-30; d = 1 / d; h *= d * c;
    aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2));
    d = 1 + aa * d; if (Math.abs(d) < 1e-30) d = 1e-30; c = 1 + aa / c; if (Math.abs(c) < 1e-30) c = 1e-30; d = 1 / d; del = d * c; h *= del;
    if (Math.abs(del - 1) < 3e-9) break;
  }
  return h;
}
function ibeta(a, b, x) {
  if (x <= 0) return 0; if (x >= 1) return 1;
  var bt = Math.exp(lgamma(a + b) - lgamma(a) - lgamma(b) + a * Math.log(x) + b * Math.log(1 - x));
  return x < (a + 1) / (a + b + 2) ? bt * betacf(a, b, x) / a : 1 - bt * betacf(b, a, 1 - x) / b;
}
function ols(v) {                            // least-squares slope per decade + two-sided t-test p
  var n = v.length, mid = (n - 1) / 2, mean = 0, k, sxx = 0, sxy = 0, sse = 0;
  for (k = 0; k < n; k++) mean += v[k] / n;
  for (k = 0; k < n; k++) { sxx += (k - mid) * (k - mid); sxy += (k - mid) * (v[k] - mean); }
  var b = sxy / sxx; for (k = 0; k < n; k++) { var e = v[k] - mean - b * (k - mid); sse += e * e; }
  var t = b / Math.sqrt(sse / (n - 2) / sxx), df = n - 2;
  return { slope: b * 10, p: isFinite(t) ? ibeta(df / 2, 0.5, df / (df + t * t)) : 1 };
}

/* ---------------- helpers ---------------- */
function readTheme() {
  var css = getComputedStyle(document.documentElement);
  ["sheet", "paper", "ink", "ink-2", "rule", "probe", "land", "neg", "mid", "pos"].forEach(function (k) { C[k] = css.getPropertyValue("--" + k).trim(); });
}
function rgb(h) { h = h.replace("#", ""); return [parseInt(h.substr(0, 2), 16), parseInt(h.substr(2, 2), 16), parseInt(h.substr(4, 2), 16)]; }
function diverge(t) {
  t = Math.max(-1, Math.min(1, t));
  var m = rgb(C.mid), p = rgb(t < 0 ? C.neg : C.pos), w = Math.pow(Math.abs(t), 0.8);
  return [m[0] + (p[0] - m[0]) * w, m[1] + (p[1] - m[1]) * w, m[2] + (p[2] - m[2]) * w];
}
function num(v, d, signed) { return (v < 0 ? "−" : (signed ? "+" : "")) + Math.abs(v).toFixed(d); }
function pText(p) { return p < 0.001 ? "p < 0.001" : "p = " + p.toFixed(3); }
function unit(v) { return v.unit ? " " + v.unit : ""; }
function deg(v, pos, neg) { return Math.abs(v).toFixed(1) + "°" + (v >= 0 ? pos : neg); }
function lonOf(i) { return meta.region.lon_west + i * meta.region.cell_deg; }
function latOf(j) { return meta.region.lat_north - j * meta.region.cell_deg; }
function cellLonLat(flat) { return deg(latOf(Math.floor(flat / nx) + 0.5), "N", "S") + ", " + deg(lonOf(flat % nx + 0.5), "E", "W"); }
function yearIndex(y) { return meta.years.indexOf(y); }
function varIndex(id) { for (var k = 0; k < meta.variables.length; k++) if (meta.variables[k].id === id) return k; return -1; }
function yearLayer(y) { return { key: "y" + y, year: y, label: String(y) }; }
function varLayer(id) { return { key: "v" + id, varId: id, label: vars[id].info.name }; }
function ease(x) { return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; }
function shownSorted() { return state.shown.slice().sort(function (a, b) { return varIndex(a) - varIndex(b); }); }
function shownContiguous() { var s = shownSorted(); for (var k = 1; k < s.length; k++) if (varIndex(s[k]) !== varIndex(s[k - 1]) + 1) return false; return true; }

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
    return state.baseline === "cell" ? (v - d.cellMean[k]) / d.scale.anomaly : (v - d.grand) / d.spatialScale;
  }
  var dv = vars[layer.varId]; return dv.trend.slope_per_decade[k] / dv.scale.slope;
}
function layerImage(layer, grey) {          // 1 pixel per grid cell, cached; grey = ghost version
  var key = layer.key + "|" + (layer.year !== undefined ? state.varId + state.baseline : "") + "|" + C.mid + (grey ? "|g" : "");
  if (imgCache[key]) return imgCache[key];
  var cv = document.createElement("canvas"); cv.width = nx; cv.height = ny;
  var g = cv.getContext("2d"), im = g.createImageData(nx, ny), land = rgb(C.land);
  for (var f = 0; f < nx * ny; f++) {
    var k = cellOf[f], c = k < 0 ? land : diverge(layerValue(layer, k)), o = f * 4;
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
function seriesFor(varId) {                 // {v: yearly values, slope per decade, p}
  var d = vars[varId], s = state.sel, key = varId + "|" + selKey();
  if (seriesCache[key]) return seriesCache[key];
  var out;
  if (s.type === "point") out = { v: d.values.map(function (row) { return row[s.cell]; }), slope: d.trend.slope_per_decade[s.cell], p: d.trend.p[s.cell] };
  else if (s.type === "area") {
    var wsum = 0, v = d.values.map(function (row) { var a = 0; for (var q = 0; q < s.cells.length; q++) a += row[s.cells[q]] * weights[s.cells[q]]; return a; });
    s.cells.forEach(function (k) { wsum += weights[k]; }); v = v.map(function (a) { return a / wsum; });
    var fit = ols(v); out = { v: v, slope: fit.slope, p: fit.p };
  } else out = { v: d.regional.mean, slope: d.regional.slope_per_decade, p: d.regional.p };
  seriesCache[key] = out; return out;
}
function selText() {
  var s = state.sel;
  if (s.type === "point") return "Point at " + cellLonLat(meta.ocean_index[s.cell]);
  if (s.type === "area") return "Area " + deg(latOf(s.j1 + 1), "N", "S") + " to " + deg(latOf(s.j0), "N", "S") + ", " + deg(lonOf(s.i0), "E", "W") + " to " + deg(lonOf(s.i1 + 1), "E", "W") + " (average of " + s.cells.length + " cells)";
  return "Average over the whole " + meta.region.name;
}

/* ---------------- geometry ---------------- */
function stackGeom(w, h) {
  var pad = 14, gap = h / 4.9, unitW = nx * ISO.ux - ny * ISO.vx;
  var s = Math.max(1, Math.min((w - 2 * pad) / unitW, (gap - 46) / (ny * ISO.vy)));
  return { s: s, gap: gap, ox: (w - unitW * s) / 2 - ny * ISO.vx * s, top: 0.5 * gap + 6 };
}
function isoMatrix(G, pos) { return [ISO.ux * G.s, ISO.uy * G.s, ISO.vx * G.s, ISO.vy * G.s, G.ox, G.top + pos * G.gap]; }
function flatMatrix(w, h) { var pad = 40, sc = Math.min((w - 2 * pad) / nx, (h - 2 * pad) / ny); return [sc, 0, 0, sc, (w - nx * sc) / 2, (h - ny * sc) / 2]; }
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
function drawSelection(g, M, alpha) {
  var s = state.sel; g.save(); g.globalAlpha = alpha;
  if (s.type === "point") { var f = meta.ocean_index[s.cell], p = apply(M, f % nx + 0.5, Math.floor(f / nx) + 0.5);
    g.beginPath(); g.arc(p[0], p[1], 5, 0, 7); g.fillStyle = C.probe; g.fill(); g.strokeStyle = C.sheet; g.lineWidth = 2; g.stroke(); }
  if (s.type === "area") { quad(g, M, s.i0, s.j0, s.i1 + 1, s.j1 + 1); g.strokeStyle = C.sheet; g.lineWidth = 4; g.stroke(); g.strokeStyle = C.probe; g.lineWidth = 2; g.stroke(); }
  g.restore();
}

/* ---------------- main view ---------------- */
function isSelected(L) { return (L.year !== undefined && L.year === state.year) || (L.varId !== undefined && L.varId === state.varId); }
function flatLayer() { return state.mode === "trend" ? varLayer(state.varId) : state.focus; }
function isFlat() { return state.mode === "trend" || (state.focus && view.focusT >= 1); }
function drawMain() {
  if (!meta) return;
  var f = fitCanvas(canvas), g = f.g, FM = flatMatrix(f.w, f.h);
  canvas.classList.toggle("flat", !!isFlat());
  if (state.mode === "trend") { geom = { flat: FM }; drawFlat(g, FM, varLayer(state.varId)); return; }
  var G = stackGeom(f.w, f.h), list = slots(), t = ease(view.focusT), fade = 1 - t;
  geom = { G: G, slots: list, flat: view.focusT >= 1 ? FM : null };
  list.slice().sort(function (a, b) { return b.pos - a.pos; }).forEach(function (sl) {
    if (state.focus && sl.layer.key === state.focus.key && view.focusT > 0) return;
    var M = isoMatrix(G, sl.pos), sel = isSelected(sl.layer) && sl.w > 0.5;
    blit(g, M, layerImage(sl.layer, true), fade * (1 - sl.w) * 0.6);
    blit(g, M, layerImage(sl.layer, false), fade * sl.w);
    g.save(); g.globalAlpha = fade * (0.35 + 0.65 * sl.w);
    quad(g, M, 0, 0, nx, ny); g.strokeStyle = sel ? C.ink : C["ink-2"]; g.lineWidth = sel ? 2 : 0.8; g.stroke();
    var lx = 14, ly = M[5] - 12, note = sl.w > 0.5 ? layerNote(sl.layer) : "";
    text(g, sl.layer.label, lx, ly, { color: C.ink, weight: sel ? "500" : "400", size: 13 });
    if (note) text(g, note, lx + g.measureText(sl.layer.label).width + 12, ly, { size: 11 });
    g.restore();
    if (sl.w > 0.5) drawSelection(g, M, fade);
  });
  if (state.focus && view.focusT > 0) {
    var A = isoMatrix(G, view.focusPos), M2 = A.map(function (a, k) { return a + (FM[k] - a) * t; });
    if (view.focusT >= 1) drawFlat(g, FM, state.focus);
    else { blit(g, M2, layerImage(state.focus, false), 1); quad(g, M2, 0, 0, nx, ny); g.strokeStyle = C.ink; g.lineWidth = 1.5; g.stroke(); }
  }
}
function layerNote(L) {                      // short reading for the current selection, next to the layer name
  if (state.sel.type === "region") return "";
  var where = state.sel.type === "point" ? "at point" : "in area";
  if (L.year !== undefined) { var d = vars[state.varId]; return num(seriesFor(state.varId).v[yearIndex(L.year)], d.info.decimals) + unit(d.info) + " " + where; }
  var s = seriesFor(L.varId); return s.p < ALPHA ? (s.slope > 0 ? "↑ rising " : "↓ falling ") + where : "no significant trend " + where;
}
function hatchPattern(g) {
  var cv = document.createElement("canvas"); cv.width = cv.height = 6; var h = cv.getContext("2d");
  h.strokeStyle = C.ink; h.globalAlpha = 0.6; h.lineWidth = 1; h.beginPath(); h.moveTo(-1, 7); h.lineTo(7, -1); h.stroke();
  return g.createPattern(cv, "repeat");
}
function drawFlat(g, M, layer) {             // front-facing map: hatching, axis ticks, selection
  var sc = M[0], x0 = M[4], y0 = M[5], k;
  blit(g, M, layerImage(layer, false), 1);
  if (layer.varId !== undefined) {
    var d = vars[layer.varId]; g.fillStyle = hatchPattern(g);
    for (k = 0; k < nOcean; k++) if (d.trend.p[k] >= ALPHA) { var fl = meta.ocean_index[k]; g.fillRect(x0 + (fl % nx) * sc, y0 + Math.floor(fl / nx) * sc, sc + 0.5, sc + 0.5); }
  }
  g.strokeStyle = C["ink-2"]; g.lineWidth = 0.8; g.strokeRect(x0, y0, nx * sc, ny * sc);
  var r = meta.region, lon, lat;
  for (lon = Math.ceil(r.lon_west / 10) * 10; lon <= lonOf(nx); lon += 10) text(g, deg(lon, "E", "W").replace(".0", ""), x0 + (lon - r.lon_west) / r.cell_deg * sc, y0 + ny * sc + 12, { align: "center", size: 10 });
  for (lat = Math.floor(r.lat_north / 5) * 5; lat >= latOf(ny); lat -= 5) text(g, deg(lat, "N", "S").replace(".0", ""), x0 - 6, y0 + (r.lat_north - lat) / r.cell_deg * sc, { align: "right", size: 10 });
  text(g, layer.label + (layer.year !== undefined ? " · " + vars[state.varId].info.name : " · trend per decade"), x0, y0 - 14, { color: C.ink, weight: "500", size: 13 });
  drawSelection(g, M, 1);
  if (drag && drag.moved) { var a = drag.a, b = drag.b; g.save(); quad(g, M, Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]) + 1, Math.max(a[1], b[1]) + 1);
    g.fillStyle = C.probe; g.globalAlpha = 0.18; g.fill(); g.globalAlpha = 1; g.strokeStyle = C.probe; g.lineWidth = 1.5; g.setLineDash([4, 3]); g.stroke(); g.restore(); }
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
function tween(key, to, dur, done) {
  if (reduced || !dur) { view[key] = to; delete tw[key]; drawMain(); if (done) done(); return; }
  tw[key] = { from: view[key], to: to, t0: performance.now(), dur: dur, done: done };
  if (!raf) raf = requestAnimationFrame(tick);
}
function tick(now) {
  raf = 0; var finished = [];
  Object.keys(tw).forEach(function (key) { var a = tw[key], x = Math.min(1, (now - a.t0) / a.dur);
    view[key] = a.from + (a.to - a.from) * (key === "focusT" ? x : ease(x)); if (x >= 1) { delete tw[key]; finished.push(a); } });
  drawMain(); finished.forEach(function (a) { if (a.done) a.done(); });
  if (Object.keys(tw).length && !raf) raf = requestAnimationFrame(tick);
}

/* ---------------- right panel ---------------- */
function drawSeries(cv, s, info, yi, compact) {
  var f = fitCanvas(cv), g = f.g, yrs = meta.years, n = yrs.length;
  var L = 50, R = 8, T = compact ? 6 : 12, B = compact ? 16 : 24, lo = Math.min.apply(null, s.v), hi = Math.max.apply(null, s.v), padY = (hi - lo) * 0.12 || 1;
  lo -= padY; hi += padY;
  var X = function (k) { return L + (f.w - L - R) * k / (n - 1); }, Y = function (v) { return T + (f.h - T - B) * (hi - v) / (hi - lo); };
  var rows = compact ? 2 : 3;
  for (var q = 0; q <= rows; q++) { var val = lo + (hi - lo) * q / rows; g.beginPath(); g.moveTo(L, Y(val)); g.lineTo(f.w - R, Y(val)); g.strokeStyle = C.rule; g.lineWidth = 0.8; g.stroke(); text(g, num(val, Math.min(info.decimals, 3)), L - 6, Y(val), { align: "right", size: 10 }); }
  [0, Math.floor((n - 1) / 2), n - 1].forEach(function (k) { text(g, String(yrs[k]), X(k), f.h - 7, { align: k === 0 ? "left" : (k === n - 1 ? "right" : "center"), size: 10 }); });
  if (yi >= 0) { g.beginPath(); g.moveTo(X(yi), T); g.lineTo(X(yi), f.h - B); g.strokeStyle = C["ink-2"]; g.lineWidth = 1; g.setLineDash([2, 3]); g.stroke(); g.setLineDash([]); }
  g.beginPath(); s.v.forEach(function (v, k) { if (k) g.lineTo(X(k), Y(v)); else g.moveTo(X(k), Y(v)); }); g.strokeStyle = C.ink; g.lineWidth = compact ? 1.5 : 2; g.lineJoin = "round"; g.stroke();
  s.v.forEach(function (v, k) { g.beginPath(); g.arc(X(k), Y(v), k === yi ? 5 : (compact ? 1.8 : 2.5), 0, 7); g.fillStyle = C.ink; g.fill(); if (k === yi) { g.strokeStyle = C.sheet; g.lineWidth = 2; g.stroke(); } });
  var mean = s.v.reduce(function (a, b) { return a + b; }, 0) / n, perYear = s.slope / 10, mid = (n - 1) / 2;
  g.beginPath(); g.moveTo(X(0), Y(mean - perYear * mid)); g.lineTo(X(n - 1), Y(mean + perYear * mid)); g.strokeStyle = C.probe; g.lineWidth = 2; g.setLineDash([6, 4]); g.stroke(); g.setLineDash([]);
}
function rate(s, info) { return num(s.slope, info.decimals + 1, true) + unit(info) + " per decade"; }
function updatePanel() {
  if (!state.panelOpen) return;
  var multi = state.mode === "vars", info = vars[state.varId].info, yi = state.mode === "time" ? yearIndex(state.year) : -1;
  $("panel-title").textContent = multi ? "Stacked variables" : info.name + (info.unit ? " (" + info.unit + ")" : "");
  $("panel-where").textContent = selText();
  $("clear").hidden = state.sel.type === "region";
  $("single").hidden = multi; $("multi").hidden = !multi;
  if (multi) {
    var box = $("multi"); box.textContent = "";
    shownSorted().forEach(function (id) {
      var d = vars[id], s = seriesFor(id), div = document.createElement("div"), h = document.createElement("h3"), cv = document.createElement("canvas"), p = document.createElement("p");
      div.className = "mini"; h.textContent = d.info.name + (d.info.unit ? " (" + d.info.unit + ")" : "");
      p.textContent = (s.slope > 0 ? "↑ " : "↓ ") + rate(s, d.info) + " · " + (s.p < ALPHA ? "significant" : "not significant") + " (" + pText(s.p) + ")";
      div.appendChild(h); div.appendChild(cv); div.appendChild(p); box.appendChild(div); drawSeries(cv, s, d.info, -1, true);
    });
  } else {
    var s1 = seriesFor(state.varId), sig = s1.p < ALPHA;
    $("verdict").textContent = sig
      ? (s1.slope > 0 ? "Rising: " : "Falling: ") + rate(s1, info) + ". This trend is statistically significant (" + pText(s1.p) + ")."
      : "Slope of " + rate(s1, info) + ", but not statistically significant (" + pText(s1.p) + "). The change could be chance; a longer record might settle it.";
    drawSeries($("chart"), s1, info, yi, false);
  }
  var tb = $("allvars"); tb.textContent = "";
  meta.variables.forEach(function (v) {
    var s = seriesFor(v.id), tr = document.createElement("tr"), on = multi ? state.shown.indexOf(v.id) >= 0 : v.id === state.varId;
    if (on) tr.className = "on";
    [[v.name, ""], [(s.slope > 0 ? "↑ " : "↓ ") + num(s.slope, v.decimals + 1, true) + unit(v), "num"], [s.p < ALPHA ? "Yes" : "No", ""]].forEach(function (c) { var td = document.createElement("td"); td.textContent = c[0]; if (c[1]) td.className = c[1]; tr.appendChild(td); });
    tr.addEventListener("click", function () { pickVar(v.id); });
    tb.appendChild(tr);
  });
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
    [["box", ""], ["name", v.name], ["unit", v.unit]].forEach(function (p) { var s = document.createElement("span"); s.className = p[0]; s.textContent = p[1]; b.appendChild(s); });
    b.addEventListener("click", function () { pickVar(v.id); }); li.appendChild(b); ul.appendChild(li);
  });
}
function updateSidebar() {
  var info = vars[state.varId].info, multi = state.mode === "vars";
  $("varlist").classList.toggle("multi", multi);
  $("var-hint").textContent = multi ? "Tick up to " + VISIBLE + " variables to stack and compare." : "Choose one variable.";
  document.querySelectorAll("#varlist button").forEach(function (b) {
    var on = multi ? state.shown.indexOf(b.dataset.id) >= 0 : b.dataset.id === state.varId; b.setAttribute("aria-pressed", String(on));
  });
  $("definition").textContent = info.name + ": " + info.definition;
  $("source").textContent = "Source: " + info.source;
}
function updateLegend() {
  var d = vars[state.varId], info = d.info, el = $("legend"), parts = [], fl = flatLayer();
  function ramp(a, b) { return "<span>" + a + "<span class=\"ramp\"></span>" + b + "</span>"; }
  if (state.mode === "time") {
    var own = state.baseline === "cell";
    parts.push(ramp("below average", "above"), "<span>same range on every layer: ±" + num(own ? d.scale.anomaly : d.spatialScale, info.decimals) + unit(info) + "</span>",
      "<span>Average of: <span class=\"seg small\" id=\"baseline\"><button type=\"button\" data-b=\"cell\" aria-pressed=\"" + own + "\">each place, all years</button><button type=\"button\" data-b=\"all\" aria-pressed=\"" + !own + "\">whole sea, all years</button></span></span>");
  } else parts.push(ramp("falling", "rising"), state.mode === "trend" ? "<span>range ±" + d.scale.slope + unit(info) + " per decade</span>" : "<span>one fixed range per variable (units differ)</span>");
  if (fl && fl.varId !== undefined && isFlat()) parts.push("<span><span class=\"sw hatch\"></span>not significant (p ≥ " + ALPHA + ")</span>");
  parts.push("<span><span class=\"sw land\"></span>land</span>", "<span><span class=\"sw probe\"></span>selection</span>");
  el.innerHTML = parts.join("");
  var bl = $("baseline"); if (bl) bl.querySelectorAll("button").forEach(function (b) { b.addEventListener("click", function () { state.baseline = b.dataset.b; render(); }); });
}
function updateStackBar() {
  var bar = $("stackbar"), trend = state.mode === "trend"; bar.hidden = false;
  $("back").hidden = !state.focus; $("up").hidden = $("down").hidden = $("scroll-pos").hidden = trend;
  var hint = $("stack-hint");
  if (trend) { hint.textContent = "Click a point or drag a box on the map to analyse it."; return; }
  var nYears = meta.years.length, nVars = meta.variables.length;
  if (state.focus) {
    $("scroll-pos").textContent = state.focus.label;
    var seq = focusSequence(), at = seq.indexOf(state.focus.year !== undefined ? state.focus.year : state.focus.varId);
    $("up").disabled = at <= 0; $("down").disabled = at >= seq.length - 1;
    hint.textContent = "Click a point or drag a box.";
  } else if (state.mode === "time") {
    var newest = meta.years[nYears - 1 - state.offset], oldest = meta.years[nYears - VISIBLE - state.offset];
    $("scroll-pos").textContent = oldest + "–" + newest;
    $("up").disabled = state.offset <= 0; $("down").disabled = state.offset >= nYears - VISIBLE;
    hint.textContent = "Click a map to open it.";
  } else {
    var s = shownSorted(), first = varIndex(s[0]), contiguous = shownContiguous() && s.length === VISIBLE;
    $("scroll-pos").textContent = s.length + " of " + nVars + " shown";
    $("up").disabled = contiguous && first <= 0; $("down").disabled = contiguous && first >= nVars - VISIBLE;
    hint.textContent = "Click a map to open it.";
  }
}

/* ---------------- state changes ---------------- */
function focusSequence() { return state.mode === "time" ? meta.years.slice().reverse() : shownSorted(); }
function step(dir) {                          // dir +1 = down the stack (older / next variable)
  if (state.mode === "trend") return;
  if (state.focus) {
    var seq = focusSequence(), at = seq.indexOf(state.focus.year !== undefined ? state.focus.year : state.focus.varId) + dir;
    if (at < 0 || at >= seq.length) return;
    if (state.mode === "time") { state.year = seq[at]; state.focus = yearLayer(seq[at]); revealYear(); view.offTime = state.offset; }
    else { state.varId = seq[at]; state.focus = varLayer(seq[at]); }
    render(); return;
  }
  if (state.mode === "time") {
    var o = Math.max(0, Math.min(meta.years.length - VISIBLE, state.offset + dir));
    if (o === state.offset) return; state.offset = o; tween("offTime", o, MOVE_MS); renderChrome(); return;
  }
  var s = shownSorted(), n = meta.variables.length, was = shownContiguous() && s.length === VISIBLE, start;
  if (was) start = varIndex(s[0]) + dir; else start = dir > 0 ? varIndex(s[0]) : varIndex(s[s.length - 1]) - VISIBLE + 1;
  start = Math.max(0, Math.min(n - VISIBLE, start));
  state.shown = meta.variables.slice(start, start + VISIBLE).map(function (v) { return v.id; });
  if (state.shown.indexOf(state.varId) < 0) state.varId = state.shown[0];
  if (was) tween("offVars", start, MOVE_MS); else { view.offVars = start; drawMain(); }
  renderChrome();
}
function revealYear() {
  var idx = meta.years.length - 1 - yearIndex(state.year), o = state.offset;
  if (idx < o) o = idx; if (idx >= o + VISIBLE) o = idx - VISIBLE + 1; state.offset = o;
}
function syncVarsOffset() { if (shownContiguous()) { view.offVars = varIndex(shownSorted()[0]); delete tw.offVars; } }
function pickVar(id) {                        // sidebar / table click
  if (state.mode === "vars") {
    var at = state.shown.indexOf(id);
    if (at >= 0) { if (state.shown.length > 1) state.shown.splice(at, 1); }
    else { if (state.shown.length >= VISIBLE) state.shown.shift(); state.shown.push(id); }
    state.varId = at >= 0 && state.shown.length ? (state.shown.indexOf(state.varId) >= 0 ? state.varId : state.shown[0]) : id;
    if (state.focus && state.shown.indexOf(state.focus.varId) < 0) { state.focus = null; view.focusT = 0; }
    syncVarsOffset();
  } else state.varId = id;
  render();
}
function setMode(m) {
  state.mode = m; state.focus = null; view.focusT = 0; delete tw.focusT;
  if (m === "vars" && state.shown.indexOf(state.varId) < 0) { state.shown.shift(); state.shown.push(state.varId); }
  if (m === "vars") syncVarsOffset();
  if (m === "time") { revealYear(); view.offTime = state.offset; }
  render();
}
function openFocus(slot) {
  var L = slot.layer; state.focus = L; view.focusPos = slot.pos; view.focusT = 0;
  if (L.year !== undefined) state.year = L.year; else state.varId = L.varId;
  if (!state.panelOpen) setPanel(true);
  renderChrome(); tween("focusT", 1, POP_MS, render);
}
function closeFocus() {
  if (!state.focus) return;
  var key = state.focus.key, found = slots().filter(function (s) { return s.layer.key === key; })[0];
  view.focusPos = found ? found.pos : 0;
  tween("focusT", 0, POP_MS, function () { state.focus = null; render(); });
}
function setSel(sel) { state.sel = sel; render(); }
function setPanel(open) {
  state.panelOpen = open; $("app").classList.toggle("panel-closed", !open);
  var b = $("panel-toggle"); b.innerHTML = open ? "&rsaquo;" : "&lsaquo;"; b.setAttribute("aria-expanded", String(open));
  b.title = open ? "Hide graph panel" : "Show graph panel"; b.setAttribute("aria-label", b.title);
}
function renderChrome() {
  document.querySelectorAll("#modes button").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.mode === state.mode)); });
  updateSidebar(); updateLegend(); updateStackBar(); updatePanel();
}
function render() { if (!meta) return; readTheme(); drawMain(); renderChrome(); }

/* ---------------- events ---------------- */
function pos(e) { var r = canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }
canvas.addEventListener("pointerdown", function (e) {
  if (!isFlat()) return; var p = pos(e), h = hit(p[0], p[1]); if (!h) return;
  drag = { a: [h.i, h.j], b: [h.i, h.j], px: p, moved: false }; canvas.setPointerCapture(e.pointerId);
});
canvas.addEventListener("pointermove", function (e) {
  var p = pos(e);
  if (drag) {
    var ij = invert(geom.flat, p[0], p[1]);
    drag.b = [Math.max(0, Math.min(nx - 1, Math.floor(ij[0]))), Math.max(0, Math.min(ny - 1, Math.floor(ij[1])))];
    if (Math.abs(p[0] - drag.px[0]) + Math.abs(p[1] - drag.px[1]) > 6) drag.moved = true;
    drawMain(); tip.style.opacity = 0; return;
  }
  var h = hit(p[0], p[1]); if (!h) { tip.style.opacity = 0; return; }
  var s;
  if (h.cell < 0) s = h.layer.label + (h.flat < 0 ? "" : " · land");
  else if (h.layer.year !== undefined) { var d = vars[state.varId]; s = h.layer.label + " · " + cellLonLat(h.flat) + " · " + num(d.values[yearIndex(h.layer.year)][h.cell], d.info.decimals) + unit(d.info); }
  else { var dv = vars[h.layer.varId]; s = dv.info.name + " · " + cellLonLat(h.flat) + " · " + num(dv.trend.slope_per_decade[h.cell], dv.info.decimals + 1, true) + unit(dv.info) + " per decade, " + (dv.trend.p[h.cell] < ALPHA ? "significant" : "not significant"); }
  tip.textContent = s; tip.style.left = Math.min(e.clientX, window.innerWidth - 400) + "px"; tip.style.top = e.clientY + "px"; tip.style.opacity = 1;
});
canvas.addEventListener("pointerup", function (e) {
  if (drag) {
    var d = drag; drag = null;
    if (!d.moved) { var k = cellOf[d.a[1] * nx + d.a[0]]; if (k >= 0) { setSel({ type: "point", cell: k }); return; } drawMain(); return; }
    var i0 = Math.min(d.a[0], d.b[0]), i1 = Math.max(d.a[0], d.b[0]), j0 = Math.min(d.a[1], d.b[1]), j1 = Math.max(d.a[1], d.b[1]), cells = [];
    for (var j = j0; j <= j1; j++) for (var i = i0; i <= i1; i++) { var c = cellOf[j * nx + i]; if (c >= 0) cells.push(c); }
    if (cells.length) setSel({ type: "area", i0: i0, i1: i1, j0: j0, j1: j1, cells: cells }); else drawMain();
    return;
  }
  if (isFlat() || view.focusT > 0) return;
  var p = pos(e), h = hit(p[0], p[1]); if (!h) return;
  if (h.slot.w > 0.5) openFocus(h.slot); else step(h.slot.pos < 0 ? -1 : 1);
});
canvas.addEventListener("pointerleave", function () { tip.style.opacity = 0; });
var wheelAcc = 0, wheelAt = 0;
canvas.addEventListener("wheel", function (e) {
  if (state.mode === "trend") return; e.preventDefault(); wheelAcc += e.deltaY;
  var now = Date.now(); if (Math.abs(wheelAcc) >= 50 && now - wheelAt > 160) { step(wheelAcc > 0 ? 1 : -1); wheelAcc = 0; wheelAt = now; }
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
$("panel-toggle").addEventListener("click", function () { setPanel(!state.panelOpen); render(); });
if (window.ResizeObserver) new ResizeObserver(function () { render(); }).observe($("stage"));
if (window.matchMedia) window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", render);
new MutationObserver(render).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
if (document.fonts && document.fonts.ready) document.fonts.ready.then(render);
window.TrendDetective = { state: state, view: view, seriesFor: seriesFor, ols: ols };   // for debugging in the console
load();
})();
