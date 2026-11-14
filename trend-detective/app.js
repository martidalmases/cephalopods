/* Trend Detective concept. Reads data/meta.json + one JSON per variable
   (format: DATA_FORMAT.md) and draws three views. No build step, no dependencies.
   Code generated with AI assistance. */
(function () {
"use strict";

var DATA_DIR = "data/";
var VISIBLE = 4;                 // layers shown in a stack at once
var ALPHA = 0.05;                // significance level
var ISO = { ux: 1, uy: 0.16, vx: -0.5, vy: 0.40 };   // screen vectors of one grid step east / south

var meta, vars = {}, cellOf, nx, ny, nOcean;
var state = { mode: "time", varId: null, year: null, cell: null, scope: "region",
              offset: { time: 0, vars: 0 }, panelOpen: true };
var $ = function (id) { return document.getElementById(id); };
var view = $("view"), chart = $("chart"), tip = $("tip");
var C = {}, geom = null, imgCache = {};

/* ---------------- loading ---------------- */
function getJSON(path) { return fetch(path).then(function (r) { if (!r.ok) throw new Error(path + " " + r.status); return r.json(); }); }
function load() {
  getJSON(DATA_DIR + "meta.json").then(function (m) {
    meta = m; nx = m.region.nx; ny = m.region.ny; nOcean = m.ocean_index.length;
    cellOf = new Int32Array(nx * ny).fill(-1);
    m.ocean_index.forEach(function (flat, k) { cellOf[flat] = k; });
    return Promise.all(m.variables.map(function (v) {
      return getJSON(DATA_DIR + v.file).then(function (d) {
        var mean = new Float64Array(nOcean), n = d.values.length;
        for (var y = 0; y < n; y++) for (var k = 0; k < nOcean; k++) mean[k] += d.values[y][k] / n;
        d.cellMean = mean; d.info = v; vars[v.id] = d;
      });
    }));
  }).then(function () {
    state.varId = meta.variables[0].id;
    state.year = meta.years[meta.years.length - 1];
    $("loading").hidden = true;
    buildSidebar(); render();
  }).catch(function (e) {
    $("loading").textContent = "Could not load the data files. Serve this folder over HTTP (python3 -m http.server) and reload.";
    console.error(e);
  });
}

/* ---------------- helpers ---------------- */
function readTheme() {
  var css = getComputedStyle(document.documentElement);
  ["sheet", "ink", "ink-2", "rule", "probe", "land", "neg", "mid", "pos"].forEach(function (k) { C[k] = css.getPropertyValue("--" + k).trim(); });
}
function rgb(h) { h = h.replace("#", ""); return [parseInt(h.substr(0, 2), 16), parseInt(h.substr(2, 2), 16), parseInt(h.substr(4, 2), 16)]; }
function diverge(t) {                       // t in [-1, 1] -> [r, g, b]
  t = Math.max(-1, Math.min(1, t));
  var m = rgb(C.mid), p = rgb(t < 0 ? C.neg : C.pos), w = Math.pow(Math.abs(t), 0.8);
  return [m[0] + (p[0] - m[0]) * w, m[1] + (p[1] - m[1]) * w, m[2] + (p[2] - m[2]) * w];
}
function num(v, d, signed) {
  var s = Math.abs(v).toFixed(d);
  return (v < 0 ? "−" : (signed ? "+" : "")) + s;
}
function pText(p) { return p < 0.001 ? "p < 0.001" : "p = " + p.toFixed(3); }
function unit(v) { return v.unit ? " " + v.unit : ""; }
function cellLonLat(flat) {
  var i = flat % nx, j = Math.floor(flat / nx), r = meta.region;
  var lon = r.lon_west + (i + 0.5) * r.cell_deg, lat = r.lat_north - (j + 0.5) * r.cell_deg;
  return Math.abs(lat).toFixed(2) + "°" + (lat >= 0 ? "N" : "S") + ", " + Math.abs(lon).toFixed(2) + "°" + (lon >= 0 ? "E" : "W");
}
function yearIndex(y) { return meta.years.indexOf(y); }
function layerList() {                      // top of the stack first
  if (state.mode === "time") return meta.years.slice().reverse().map(function (y) { return { key: "y" + y, year: y, label: String(y) }; });
  return meta.variables.map(function (v) { return { key: "v" + v.id, varId: v.id, label: v.name }; });
}
function visibleLayers() { var o = state.offset[state.mode] || 0; return layerList().slice(o, o + VISIBLE); }
function layerValue(layer, k) {             // normalised colour value for ocean cell k
  if (layer.year !== undefined) { var d = vars[state.varId]; return (d.values[yearIndex(layer.year)][k] - d.cellMean[k]) / d.scale.anomaly; }
  var dv = vars[layer.varId]; return dv.trend.slope_per_decade[k] / dv.scale.slope;
}
function layerImage(layer) {                // 1 pixel per grid cell, cached
  var key = layer.key + "|" + (layer.year !== undefined ? state.varId : "") + "|" + C.mid;
  if (imgCache[key]) return imgCache[key];
  var cv = document.createElement("canvas"); cv.width = nx; cv.height = ny;
  var g = cv.getContext("2d"), im = g.createImageData(nx, ny), land = rgb(C.land);
  for (var f = 0; f < nx * ny; f++) {
    var k = cellOf[f], c = k < 0 ? land : diverge(layerValue(layer, k)), o = f * 4;
    im.data[o] = c[0]; im.data[o + 1] = c[1]; im.data[o + 2] = c[2]; im.data[o + 3] = k < 0 ? 150 : 255;
  }
  g.putImageData(im, 0, 0); imgCache[key] = cv; return cv;
}
function fitCanvas(cv) {
  var d = window.devicePixelRatio || 1, w = cv.clientWidth, h = cv.clientHeight;
  if (cv.width !== Math.round(w * d) || cv.height !== Math.round(h * d)) { cv.width = Math.round(w * d); cv.height = Math.round(h * d); }
  var g = cv.getContext("2d"); g.setTransform(d, 0, 0, d, 0, 0); g.clearRect(0, 0, w, h);
  return { g: g, w: w, h: h, d: d };
}
function text(g, s, x, y, o) {
  o = o || {}; g.font = (o.weight || "400") + " " + (o.size || 11) + "px 'IBM Plex Mono',monospace";
  g.fillStyle = o.color || C["ink-2"]; g.textAlign = o.align || "left"; g.textBaseline = o.base || "middle"; g.fillText(s, x, y);
}

/* ---------------- main view: stack ---------------- */
function drawStack() {
  var f = fitCanvas(view), g = f.g, layers = visibleLayers(), n = layers.length;
  var padL = 16, padR = 250, padV = 24;
  var unitW = nx * ISO.ux + ny * -ISO.vx, unitH = nx * ISO.uy + ny * ISO.vy;
  var s = Math.min((f.w - padL - padR) / unitW, (f.h - 2 * padV) / (unitH * (1 + 0.5 * (VISIBLE - 1))));
  s = Math.max(s, 1);
  var layerH = unitH * s, gap = Math.min((f.h - 2 * padV - layerH) / (VISIBLE - 1), layerH * 0.95);
  var top = (f.h - (layerH + gap * (VISIBLE - 1))) / 2, ox = padL + ny * -ISO.vx * s;
  geom = { type: "stack", s: s, ox: ox, oys: [], layers: layers };
  function P(i, j, oy) { return [ox + (i * ISO.ux + j * ISO.vx) * s, oy + (i * ISO.uy + j * ISO.vy) * s]; }
  for (var k = n - 1; k >= 0; k--) {                    // bottom layer first
    var oy = top + k * gap, L = layers[k]; geom.oys[k] = oy;
    g.save(); g.transform(ISO.ux * s, ISO.uy * s, ISO.vx * s, ISO.vy * s, ox, oy);
    g.imageSmoothingEnabled = false; g.drawImage(layerImage(L), 0, 0); g.restore();
    var sel = (L.year !== undefined && L.year === state.year) || (L.varId && L.varId === state.varId);
    var a = P(0, 0, oy), b = P(nx, 0, oy), c = P(nx, ny, oy), d = P(0, ny, oy);
    g.beginPath(); g.moveTo(a[0], a[1]); g.lineTo(b[0], b[1]); g.lineTo(c[0], c[1]); g.lineTo(d[0], d[1]); g.closePath();
    g.strokeStyle = sel ? C.ink : C["ink-2"]; g.lineWidth = sel ? 2 : 0.8; g.stroke();
    text(g, L.label, b[0] + 12, b[1] + 10, { color: C.ink, weight: sel ? "500" : "400", size: 13 });
    var sub = layerNote(L); if (sub) text(g, sub, b[0] + 12, b[1] + 27);
  }
  if (state.cell !== null && n) {                        // probe through every visible layer
    var flat = meta.ocean_index[state.cell], ci = flat % nx + 0.5, cj = Math.floor(flat / nx) + 0.5;
    var p0 = P(ci, cj, geom.oys[0]), p1 = P(ci, cj, geom.oys[n - 1]);
    g.beginPath(); g.moveTo(p0[0], p0[1] - 20); g.lineTo(p1[0], p1[1] + 10); g.strokeStyle = C.probe; g.lineWidth = 2; g.stroke();
    for (k = 0; k < n; k++) { var p = P(ci, cj, geom.oys[k]); g.beginPath(); g.arc(p[0], p[1], 5, 0, 7); g.fillStyle = C.probe; g.fill(); g.strokeStyle = C.sheet; g.lineWidth = 2; g.stroke(); }
  }
}
function layerNote(L) {                                  // second label line: reading at the probe
  if (state.cell === null) return "";
  if (L.year !== undefined) { var d = vars[state.varId]; return num(d.values[yearIndex(L.year)][state.cell], d.info.decimals) + unit(d.info) + " at probe"; }
  var dv = vars[L.varId], sl = dv.trend.slope_per_decade[state.cell], sig = dv.trend.p[state.cell] < ALPHA;
  return sig ? (sl > 0 ? "↑ rising at probe" : "↓ falling at probe") : "no significant trend at probe";
}
function hitStack(px, py) {
  if (!geom || geom.type !== "stack") return null;
  var s = geom.s, det = (ISO.ux * ISO.vy - ISO.vx * ISO.uy) * s * s;
  for (var k = 0; k < geom.layers.length; k++) {         // topmost first
    var dx = px - geom.ox, dy = py - geom.oys[k];
    var i = (dx * ISO.vy * s - ISO.vx * s * dy) / det, j = (ISO.ux * s * dy - ISO.uy * s * dx) / det;
    if (i >= 0 && i < nx && j >= 0 && j < ny) { var flat = Math.floor(j) * nx + Math.floor(i); return { layer: geom.layers[k], flat: flat, cell: cellOf[flat] }; }
  }
  return null;
}

/* ---------------- main view: trend map ---------------- */
var hatch = null;
function hatchPattern(g) {
  var cv = document.createElement("canvas"); cv.width = cv.height = 6; var h = cv.getContext("2d");
  h.strokeStyle = C.ink; h.globalAlpha = 0.6; h.lineWidth = 1; h.beginPath(); h.moveTo(-1, 7); h.lineTo(7, -1); h.stroke();
  return g.createPattern(cv, "repeat");
}
function drawTrend() {
  var f = fitCanvas(view), g = f.g, pad = 20, sc = Math.min((f.w - 2 * pad) / nx, (f.h - 2 * pad) / ny);
  var x0 = (f.w - nx * sc) / 2, y0 = (f.h - ny * sc) / 2, d = vars[state.varId];
  geom = { type: "trend", sc: sc, x0: x0, y0: y0 };
  g.imageSmoothingEnabled = false; g.drawImage(layerImage({ key: "v" + state.varId, varId: state.varId }), x0, y0, nx * sc, ny * sc);
  g.fillStyle = hatchPattern(g);
  for (var k = 0; k < nOcean; k++) if (d.trend.p[k] >= ALPHA) { var fl = meta.ocean_index[k]; g.fillRect(x0 + (fl % nx) * sc, y0 + Math.floor(fl / nx) * sc, sc + 0.5, sc + 0.5); }
  g.strokeStyle = C["ink-2"]; g.lineWidth = 0.8; g.strokeRect(x0, y0, nx * sc, ny * sc);
  if (state.cell !== null) { var flat = meta.ocean_index[state.cell], cx = x0 + (flat % nx + 0.5) * sc, cy = y0 + (Math.floor(flat / nx) + 0.5) * sc;
    g.beginPath(); g.arc(cx, cy, 6, 0, 7); g.fillStyle = C.probe; g.fill(); g.strokeStyle = C.sheet; g.lineWidth = 2; g.stroke(); }
}
function hitTrend(px, py) {
  if (!geom || geom.type !== "trend") return null;
  var i = Math.floor((px - geom.x0) / geom.sc), j = Math.floor((py - geom.y0) / geom.sc);
  if (i < 0 || i >= nx || j < 0 || j >= ny) return null;
  return { flat: j * nx + i, cell: cellOf[j * nx + i] };
}

/* ---------------- right panel ---------------- */
function currentSeries() {
  var d = vars[state.varId], point = state.scope === "point" && state.cell !== null;
  return point
    ? { v: d.values.map(function (row) { return row[state.cell]; }), slope: d.trend.slope_per_decade[state.cell], p: d.trend.p[state.cell], point: true }
    : { v: d.regional.mean, slope: d.regional.slope_per_decade, p: d.regional.p, point: false };
}
function drawChart() {
  if (!meta || !state.panelOpen) return;
  var f = fitCanvas(chart), g = f.g, s = currentSeries(), yrs = meta.years, n = yrs.length, info = vars[state.varId].info;
  var L = 52, R = 10, T = 12, B = 24, lo = Math.min.apply(null, s.v), hi = Math.max.apply(null, s.v), padY = (hi - lo) * 0.12 || 1;
  lo -= padY; hi += padY;
  var X = function (k) { return L + (f.w - L - R) * k / (n - 1); }, Y = function (v) { return T + (f.h - T - B) * (hi - v) / (hi - lo); };
  for (var q = 0; q <= 3; q++) { var val = lo + (hi - lo) * q / 3; g.beginPath(); g.moveTo(L, Y(val)); g.lineTo(f.w - R, Y(val)); g.strokeStyle = C.rule; g.lineWidth = 0.8; g.stroke(); text(g, num(val, Math.min(info.decimals, 3)), L - 6, Y(val), { align: "right", size: 10 }); }
  [0, Math.floor((n - 1) / 2), n - 1].forEach(function (k) { text(g, String(yrs[k]), X(k), f.h - 9, { align: k === 0 ? "left" : (k === n - 1 ? "right" : "center"), size: 10 }); });
  var yi = state.mode === "time" ? yearIndex(state.year) : -1;
  if (yi >= 0) { g.beginPath(); g.moveTo(X(yi), T); g.lineTo(X(yi), f.h - B); g.strokeStyle = C["ink-2"]; g.lineWidth = 1; g.setLineDash([2, 3]); g.stroke(); g.setLineDash([]); }
  g.beginPath(); s.v.forEach(function (v, k) { if (k) g.lineTo(X(k), Y(v)); else g.moveTo(X(k), Y(v)); }); g.strokeStyle = C.ink; g.lineWidth = 2; g.lineJoin = "round"; g.stroke();
  s.v.forEach(function (v, k) { g.beginPath(); g.arc(X(k), Y(v), k === yi ? 5 : 2.5, 0, 7); g.fillStyle = C.ink; g.fill(); if (k === yi) { g.strokeStyle = C.sheet; g.lineWidth = 2; g.stroke(); } });
  var mean = s.v.reduce(function (a, b) { return a + b; }, 0) / n, perYear = s.slope / 10, mid = (n - 1) / 2;
  g.beginPath(); g.moveTo(X(0), Y(mean - perYear * mid)); g.lineTo(X(n - 1), Y(mean + perYear * mid)); g.strokeStyle = C.probe; g.lineWidth = 2; g.setLineDash([6, 4]); g.stroke(); g.setLineDash([]);
}
function updatePanel() {
  var d = vars[state.varId], info = d.info, s = currentSeries(), sig = s.p < ALPHA;
  $("panel-title").textContent = info.name + (info.unit ? " (" + info.unit + ")" : "");
  $("panel-where").textContent = s.point ? "At " + cellLonLat(meta.ocean_index[state.cell]) : "Average over the whole " + meta.region.name;
  var rate = num(s.slope, info.decimals + 1, true) + unit(info) + " per decade";
  $("verdict").textContent = sig
    ? (s.slope > 0 ? "Rising: " : "Falling: ") + rate + ". This trend is statistically significant (" + pText(s.p) + ")."
    : "Slope of " + rate + ", but not statistically significant (" + pText(s.p) + "). The change could be chance; a longer record might settle it.";
  $("hint").hidden = state.cell !== null;
  document.querySelectorAll("#scope button").forEach(function (b) {
    var on = (b.dataset.scope === "point") === s.point; b.setAttribute("aria-pressed", String(on));
    if (b.dataset.scope === "point") b.disabled = state.cell === null;
  });
  var wrap = $("allvars-wrap"); wrap.hidden = state.cell === null;
  if (state.cell !== null) {
    var tb = $("allvars"); tb.textContent = "";
    meta.variables.forEach(function (v) {
      var dv = vars[v.id], sl = dv.trend.slope_per_decade[state.cell], sg = dv.trend.p[state.cell] < ALPHA, tr = document.createElement("tr");
      if (v.id === state.varId) tr.className = "on";
      [[v.name, ""], [(sl > 0 ? "↑ " : "↓ ") + num(sl, v.decimals + 1, true) + unit(v), "num"], [sg ? "Yes" : "No", ""]].forEach(function (c) { var td = document.createElement("td"); td.textContent = c[0]; if (c[1]) td.className = c[1]; tr.appendChild(td); });
      tr.addEventListener("click", function () { setVar(v.id); });
      tb.appendChild(tr);
    });
  }
  drawChart();
}

/* ---------------- sidebar, legend, scroller ---------------- */
function buildSidebar() {
  $("synthetic-flag").hidden = !meta.synthetic;
  $("region-name").textContent = meta.region.name;
  $("period").textContent = meta.years[0] + " to " + meta.years[meta.years.length - 1] + ", yearly values";
  var ul = $("varlist");
  meta.variables.forEach(function (v) {
    var li = document.createElement("li"), b = document.createElement("button"), a = document.createElement("span"), u = document.createElement("span");
    b.type = "button"; b.dataset.id = v.id; a.textContent = v.name; u.className = "unit"; u.textContent = v.unit;
    b.appendChild(a); b.appendChild(u); b.addEventListener("click", function () { setVar(v.id); }); li.appendChild(b); ul.appendChild(li);
  });
}
function updateSidebar() {
  var info = vars[state.varId].info;
  document.querySelectorAll("#varlist button").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.id === state.varId)); });
  $("definition").textContent = info.definition;
  $("source").textContent = "Source: " + info.source;
}
function updateLegend() {
  var d = vars[state.varId], info = d.info, el = $("legend"), parts = [];
  function ramp(a, b) { return "<span>" + a + "<span class=\"ramp\"></span>" + b + "</span>"; }
  if (state.mode === "time") parts.push(ramp("below this cell’s average", "above"), "<span>colour range ±" + d.scale.anomaly + unit(info) + "</span>");
  if (state.mode === "vars") parts.push(ramp("falling", "rising"), "<span>each layer uses its own colour range</span>");
  if (state.mode === "trend") parts.push(ramp("falling", "rising"), "<span>colour range ±" + d.scale.slope + unit(info) + " per decade</span>", "<span><span class=\"sw hatch\"></span>not significant (p ≥ " + ALPHA + ")</span>");
  parts.push("<span><span class=\"sw land\"></span>land</span>", "<span><span class=\"sw probe\"></span>probe</span>");
  el.innerHTML = parts.join("");
}
function updateScroller() {
  var stack = state.mode !== "trend"; $("scroller").hidden = !stack; $("scroller").style.display = stack ? "" : "none";
  if (!stack) return;
  var list = layerList(), o = state.offset[state.mode], vis = visibleLayers();
  $("up").disabled = o <= 0; $("down").disabled = o >= list.length - VISIBLE;
  $("scroll-pos").textContent = state.mode === "time" ? vis[vis.length - 1].label + "–" + vis[0].label : (o + 1) + "–" + (o + vis.length) + " of " + list.length;
}
function scrollLayers(step) {
  if (state.mode === "trend") return;
  var max = Math.max(0, layerList().length - VISIBLE), o = Math.max(0, Math.min(max, state.offset[state.mode] + step));
  if (o !== state.offset[state.mode]) { state.offset[state.mode] = o; render(); }
}
function reveal() {                                       // keep the selected layer inside the visible window
  if (state.mode === "trend") return;
  var list = layerList(), idx = -1;
  list.forEach(function (L, k) { if ((L.year !== undefined && L.year === state.year) || (L.varId && L.varId === state.varId)) idx = k; });
  if (idx < 0) return;
  var o = state.offset[state.mode];
  if (idx < o) o = idx; if (idx >= o + VISIBLE) o = idx - VISIBLE + 1;
  state.offset[state.mode] = o;
}

/* ---------------- state changes ---------------- */
function setVar(id) { state.varId = id; reveal(); render(); }
function setMode(m) { state.mode = m; reveal(); render(); }
function render() {
  if (!meta) return;
  readTheme();
  document.querySelectorAll("#modes button").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.mode === state.mode)); });
  if (state.mode === "trend") drawTrend(); else drawStack();
  updateSidebar(); updateLegend(); updateScroller(); updatePanel();
}

/* ---------------- events ---------------- */
function pos(e) { var r = view.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }
function hit(e) { var p = pos(e); return state.mode === "trend" ? hitTrend(p[0], p[1]) : hitStack(p[0], p[1]); }
view.addEventListener("click", function (e) {
  var h = hit(e); if (!h) return;
  if (h.layer) { if (h.layer.year !== undefined) state.year = h.layer.year; else state.varId = h.layer.varId; }
  if (h.cell >= 0) { state.cell = h.cell; state.scope = "point"; }
  render();
});
view.addEventListener("mousemove", function (e) {
  var h = hit(e); if (!h || !meta) { tip.style.opacity = 0; return; }
  var s;
  if (h.cell < 0) s = (h.layer ? h.layer.label + " · " : "") + "land";
  else if (h.layer && h.layer.year !== undefined) { var d = vars[state.varId]; s = h.layer.label + " · " + cellLonLat(h.flat) + " · " + num(d.values[yearIndex(h.layer.year)][h.cell], d.info.decimals) + unit(d.info); }
  else { var dv = vars[h.layer ? h.layer.varId : state.varId]; s = dv.info.name + " · " + cellLonLat(h.flat) + " · " + num(dv.trend.slope_per_decade[h.cell], dv.info.decimals + 1, true) + unit(dv.info) + " per decade, " + (dv.trend.p[h.cell] < ALPHA ? "significant" : "not significant"); }
  tip.textContent = s; tip.style.left = Math.min(e.clientX, window.innerWidth - 380) + "px"; tip.style.top = e.clientY + "px"; tip.style.opacity = 1;
});
view.addEventListener("mouseleave", function () { tip.style.opacity = 0; });
var wheelAcc = 0;
view.addEventListener("wheel", function (e) {
  if (state.mode === "trend") return; e.preventDefault(); wheelAcc += e.deltaY;
  if (Math.abs(wheelAcc) >= 40) { scrollLayers(wheelAcc > 0 ? 1 : -1); wheelAcc = 0; }
}, { passive: false });
view.addEventListener("keydown", function (e) { if (e.key === "ArrowDown") { scrollLayers(1); e.preventDefault(); } if (e.key === "ArrowUp") { scrollLayers(-1); e.preventDefault(); } });
$("up").addEventListener("click", function () { scrollLayers(-1); });
$("down").addEventListener("click", function () { scrollLayers(1); });
document.querySelectorAll("#modes button").forEach(function (b) { b.addEventListener("click", function () { setMode(b.dataset.mode); }); });
document.querySelectorAll("#scope button").forEach(function (b) { b.addEventListener("click", function () { state.scope = b.dataset.scope; render(); }); });
$("panel-toggle").addEventListener("click", function () {
  state.panelOpen = !state.panelOpen; $("app").classList.toggle("panel-closed", !state.panelOpen);
  this.textContent = state.panelOpen ? "Hide graph" : "Show graph"; this.setAttribute("aria-expanded", String(state.panelOpen)); render();
});
if (window.ResizeObserver) new ResizeObserver(function () { render(); }).observe($("stage"));
if (window.matchMedia) window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", render);
new MutationObserver(render).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
if (document.fonts && document.fonts.ready) document.fonts.ready.then(render);
load();
})();
