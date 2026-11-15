/* Species page for Trend Detective. Self-contained: it adds the "Variables | Species" switch and its own
   sidebar, map and panel, and leaves app.js untouched. All computing happens in the Python backend
   (backend/especies_habitat.py): this file asks for one JSON per species and draws it.

   What the backend sends for a species (GET /api/habitat/especies/<name>):
     - its environmental profile: percentiles of the four chosen variables at its sightings worldwide;
     - habitat suitability (0 to 100) of every 0.25° Mediterranean sea cell, every year since 1980;
     - yearly indicators with their trend: suitable area, centre and poleward limit of the habitat,
       observed poleward limit, thermal tolerance. Same method as the rest of the site: Sen's slope,
       modified Mann-Kendall test, FDR adjustment.
   A species that is not stored yet is computed on its first request (its sightings come from OBIS).

   Views: a stack of yearly maps (one species over time) or of several species (one year), an opened
   layer, the trend map, "now × trend", and on the Analysis screen the combined-change map (habitat trend
   of the species × how much the sea around it has changed, from the Variables data).
   Code written with AI assistance. */
(function () {
"use strict";

var API = "/api";      // same origin: nginx forwards /api/ to the backend
var ALPHA = 0.05;
var COMMON_NAMES = {   // the backend stores Spanish common names; the site is in English
  "Octopus vulgaris": "Common octopus", "Sepia officinalis": "Common cuttlefish",
  "Loligo vulgaris": "European squid", "Eledone moschata": "Musky octopus",
  "Eledone cirrhosa": "Horned octopus", "Illex coindetii": "Broadtail shortfin squid",
  "Todarodes sagittatus": "European flying squid"
};
// Bivariate map "now × trend": [suitability now: low, medium, high][trend: falling, none, rising].
var BI = [["#c9d6ea", null, "#f0cdc8"], ["#7fa3d6", "#b9b6ac", "#e08a80"], ["#1c5cab", "#6f6c62", "#c23b30"]];
var NOW_WORD = ["low suitability now", "medium suitability now", "high suitability now"], TREND_WORD = ["falling", "no significant trend", "rising"];
var NOW_YEARS = 5;     // "now" = mean of the last five years
var ISO = { ux: 1, uy: 0.10, vx: -0.35, vy: 0.30 };   // same slant as the Variables stack (app.js)
// Combined change (Analysis): the four species variables of the Variables data, on their 0.5° cells.
var ENV_SET = "mediterraneo-2003";       // first set with chlorophyll
var ENV_VARS = [["sst_max", "summer maximum"], ["sst_min", "winter minimum"], ["mhw", "heatwave months"], ["chl", "chlorophyll"]];
var BIV = [["#f2c4bd", "#dd7c70", "#b3261a"], ["#e8e8e8", "#b5b5b5", "#7a7a7a"], ["#c3d5ee", "#7aa5dc", "#1c5cab"]];   // [losing, stable, gaining][low, medium, high]

var $ = function (id) { return document.getElementById(id); };
function el(tag, cls, txt) { var e = document.createElement(tag); if (cls) e.className = cls; if (txt !== undefined) e.textContent = txt; return e; }
function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
function has(x) { return x !== null && x !== undefined && isFinite(x); }
function commonName(sp) { return COMMON_NAMES[sp.nombre] || sp.nombre_comun || ""; }
function rgb(h) { h = h.replace("#", ""); if (h.length === 3) h = h.replace(/./g, "$&$&"); return [0, 2, 4].map(function (i) { return parseInt(h.substr(i, 2), 16); }); }
function mix(a, b, t) { return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]; }
function median(a) { a = a.slice().sort(function (x, y) { return x - y; }); var h = a.length >> 1; return a.length % 2 ? a[h] : (a[h - 1] + a[h]) / 2; }
function quantile(list, q) { var s = list.slice().sort(function (a, b) { return a - b; }); return s[Math.floor((s.length - 1) * q)] || 0; }
function signed(x, d) { return (x > 0 ? "+" : x < 0 ? "−" : "") + Math.abs(x).toLocaleString("en-GB", { minimumFractionDigits: d, maximumFractionDigits: d }); }
function pText(p) { return p < 0.001 ? "adjusted p < 0.001" : "adjusted p = " + p.toFixed(3); }
function deg(v, pos, neg) { return Math.abs(v).toFixed(2) + "°" + (v >= 0 ? pos : neg); }

// screen: "viz" | "analysis"; mode: "time" (stack of years) | "species" (stack of species) | "trend" | "bi";
// focus: one stack layer opened flat; layer: "temp" | "chl" (variables in the suitability).
var state = { page: "trends", screen: "viz", mode: "time", focus: false, layer: "temp", species: null, year: null, data: null, list: [] };

/* ---------------- DOM: page switch, button groups, sidebar, main view, panel ---------------- */
var options = document.querySelector(".options");
var pages = el("span", "seg"); pages.id = "pages"; pages.setAttribute("role", "group"); pages.setAttribute("aria-label", "Page");
[["trends", "Variables"], ["species", "Species"]].forEach(function (p) {
  var b = el("button", "", p[1]); b.type = "button"; b.dataset.page = p[0]; b.setAttribute("aria-pressed", String(p[0] === "trends"));
  b.addEventListener("click", function () { setPage(p[0]); }); pages.appendChild(b);
});
options.insertBefore(pages, options.firstChild);

// Same two button groups as the Variables page, with the Species choices. Each page shows its own pair.
function seg(id, label, items, on) {
  var g = el("span", "seg"); g.id = id; g.hidden = true; g.setAttribute("role", "group"); g.setAttribute("aria-label", label);
  items.forEach(function (it) {
    var b = el("button", "", it[1]); b.type = "button"; b.dataset.v = it[0]; if (it[2]) b.title = it[2];
    b.addEventListener("click", function () { on(it[0]); }); g.appendChild(b);
  });
  options.appendChild(g); return g;
}
function press(g, v) { g.querySelectorAll("button").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.v === v)); }); }
var spScreens = seg("sp-screens", "Screen", [["viz", "Visualisation"], ["analysis", "Analysis"]], setScreen);
var spModes = seg("sp-modes", "Visualisation options", [["time", "Stack by time"], ["species", "Stack by species"],
  ["trend", "Trend map"], ["bi", "Now × trend", "Suitability of the last years and its trend on one map"]], setMode);
function setScreen(v) { state.screen = v; state.focus = false; setPage(state.page); }
function setMode(v) { state.mode = v; state.focus = false; refresh(); }

var trendSide = document.querySelector(".side .side-body");
var side = el("div", "side-body"); side.id = "sp-side"; side.hidden = true;
side.innerHTML =
  '<section><h2>Region</h2><p class="fact">Mediterranean Sea</p><p class="fact dim" id="sp-period">&nbsp;</p></section>' +
  '<section><h2>Species</h2><p class="fact dim">Stored species. Cephalopods are always ready.</p><ul class="varlist" id="sp-list"></ul></section>' +
  '<section class="sp-search"><h2>Find another species</h2>' +
  '<div class="sp-combo"><input id="sp-find" placeholder="Scientific name, e.g. Thalassoma pavo" autocomplete="off" spellcheck="false" ' +
  'role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="sp-suggest" aria-label="Scientific name">' +
  '<ul id="sp-suggest" role="listbox" hidden></ul></div>' +
  '<p class="fact dim">Names and record counts from OBIS. Computed on first request, then stored.</p></section>' +
  '<section id="sp-layer-box"><h2>Variables in the map</h2><span class="seg small" id="sp-layer" role="group" aria-label="Variables in the map">' +
  '<button type="button" data-layer="temp" aria-pressed="true">Temperature</button><button type="button" data-layer="chl" aria-pressed="false">+ chlorophyll</button></span>' +
  '<p class="fact dim" id="sp-layer-note">&nbsp;</p></section>' +
  '<section><h2>What is this?</h2><p class="fact dim">Every sighting of the species in the world (OBIS, since 1980) is matched to the sea conditions of its place and year. ' +
  'That gives its environmental profile: the range of conditions where it lives. Habitat suitability (0 to 1) says how close each place of the Mediterranean, each year, ' +
  'is to that profile. It is a statistical match with four variables, not proof that the species is there.</p>' +
  '<p class="fact dim side-links"><a href="index.html">Start page</a> · <a href="method.html">How we chose the four variables</a></p></section>';
trendSide.parentNode.appendChild(side);

var trendRow = $("row");
var row = el("div", "row"); row.id = "sp-row"; row.hidden = true;
row.innerHTML =
  '<main class="main"><div class="legend" id="sp-legend"></div>' +
  '<div class="stage"><canvas id="sp-map" aria-label="Habitat suitability map of the Mediterranean"></canvas><p class="loading" id="sp-status">Loading…</p></div>' +
  '<div class="stackbar"><button type="button" class="ghost" id="sp-back" hidden>&larr; Back to stack</button>' +
  '<button type="button" class="round" id="sp-prev" aria-label="Previous year">&#9664;</button>' +
  '<select id="sp-year" class="sp-select" aria-label="Year"></select>' +
  '<button type="button" class="round" id="sp-next" aria-label="Next year">&#9654;</button>' +
  '<span class="fact dim" id="sp-hint"></span></div></main>' +
  '<aside class="panel" aria-label="Species details"><div class="sp-panel">' +
  '<div><div class="panel-head"><h2 id="sp-title">&nbsp;</h2></div><p class="fact dim" id="sp-sub">&nbsp;</p></div>' +
  '<section><h2>Suitable area by year</h2><div class="sp-bars" id="sp-bars"></div><div class="sp-years" id="sp-years"></div>' +
  '<p class="verdict" id="sp-area">&nbsp;</p></section>' +
  '<section><h2>Where the suitable habitat is</h2><div id="sp-model"></div></section>' +
  '<section><h2>Thermal tolerance</h2><p class="note" id="sp-tol-note"></p><div id="sp-tol"></div></section>' +
  '<section id="sp-obs-box"><h2>Observed poleward limit</h2><div id="sp-obs"></div></section>' +
  '<section><h2>What limits its habitat</h2><ul class="sp-limits" id="sp-limits"></ul></section>' +
  '<section><h2>Conditions where it is seen</h2><table class="allvars"><thead><tr><th>Variable</th><th>Typical range</th><th>Median</th></tr></thead><tbody id="sp-niche"></tbody></table>' +
  '<p class="note">Typical range: 5th to 95th percentile of the conditions at its sightings, worldwide. Variables marked * cover every year since 1980 and make the yearly suitability; ' +
  'chlorophyll starts in 2003 (satellite) and is a separate layer. <a href="?datos=mediterraneo-1982">See the trends of these variables</a>.</p></section>' +
  '<section><h2>Where it is seen in the world</h2><canvas id="sp-world" aria-label="World map of the sightings used"></canvas><p class="note" id="sp-world-note"></p></section>' +
  '<section><h2>Place</h2><p class="fact dim sp-cell" id="sp-cell">Open a map and point at it to read a place.</p></section>' +
  '<section><p class="note" id="sp-method"></p></section>' +
  '</div></aside>';
trendRow.parentNode.appendChild(row);

function setPage(p) {
  state.page = p;
  var sp = p === "species";
  document.documentElement.dataset.page = p;                                 // species.css: accent colour of the page
  document.documentElement.dataset.portal = sp ? "species" : "trends";      // species.css: background tint of the page
  pages.querySelectorAll("button").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.page === p)); });
  trendRow.hidden = sp; trendSide.hidden = sp; $("screens").hidden = sp;
  $("modes").hidden = sp || document.querySelector('#screens [data-screen="analysis"]').getAttribute("aria-pressed") === "true";
  spScreens.hidden = !sp; spModes.hidden = !sp || state.screen === "analysis";
  row.hidden = !sp; side.hidden = !sp;
  if (history.replaceState) history.replaceState(null, "", sp ? "#species" : location.pathname + location.search);
  if (sp && !state.list.length) start(); else if (sp) refresh();
}

/* ---------------- API and the species store ---------------- */
function api(path) {
  return fetch(API + path).then(function (r) {
    if (r.ok) return r.json();
    return r.json().catch(function () { return {}; }).then(function (b) { throw new Error(b.detail || ("HTTP " + r.status)); });
  });
}

var store = {};                                 // name -> species data once loaded, or the promise while it loads
function load(name) {
  if (!store[name]) store[name] = api("/habitat/especies/" + encodeURIComponent(name)).then(function (d) {
    prepare(d); store[name] = store[d.nombre] = d; return d;
  }, function (e) { delete store[name]; throw e; });
  return Promise.resolve(store[name]);
}
function get(name) {                            // the data if it is here; else ask for it and redraw when it arrives
  var v = store[name];
  if (!v) load(name).then(draw).catch(function () { /* the stack shows it as missing */ });
  return v && !v.then ? v : null;
}

function loadList(select) {
  return api("/habitat/especies").then(function (list) {
    state.list = list;
    var ul = $("sp-list"); ul.innerHTML = "";
    list.forEach(function (sp) {
      var li = el("li"), b = el("button"); b.type = "button";
      b.setAttribute("aria-pressed", String(sp.nombre === (select || state.species)));
      var name = el("span", "name"); name.appendChild(el("span", "sp-name", sp.nombre)); name.appendChild(el("span", "sp-common", commonName(sp)));
      b.appendChild(name);
      if (has(sp.area_pendiente_decada)) { var a = el("span", "unit", sp.area_significativa ? (sp.area_pendiente_decada > 0 ? "↑ area" : "↓ area") : "="); a.title = "Trend of its suitable area in the Mediterranean"; b.appendChild(a); }
      b.addEventListener("click", function () { show(sp.nombre); });
      li.appendChild(b); ul.appendChild(li);
    });
    return list;
  });
}

function show(name) {
  state.species = name;
  status("Computing " + name + "… The first request for a species can take up to a minute (its sightings are downloaded from OBIS).");
  return load(name).then(function (d) {
    if (state.species !== name) return;
    state.data = d; state.species = d.nombre;
    if (!d.idoneidad_chl) state.layer = "temp";
    var years = layerYears(d);
    if (!state.year || years.indexOf(state.year) < 0) state.year = years[years.length - 1];
    status(""); panel(); refresh(); return loadList(d.nombre);
  }).catch(function (e) { status("Could not compute " + name + ": " + e.message); });
}

function prepare(d) {                          // lookups used by the maps
  var r = d.region, k, n = d.ocean_index.length;
  d.cellOf = new Int32Array(r.nx * r.ny).fill(-1);
  d.ocean_index.forEach(function (flat, i) { d.cellOf[flat] = i; });
  var last = d.idoneidad.slice(-NOW_YEARS);
  d.now = new Float64Array(n);
  for (k = 0; k < n; k++) { var s = 0, c = 0; last.forEach(function (rowv) { if (rowv[k] !== null) { s += rowv[k]; c++; } }); d.now[k] = c ? s / c / 100 : NaN; }
  var slopes = d.tendencia_celdas.slope_per_decade.filter(has).map(Math.abs).sort(function (a, b) { return a - b; });
  d.slopeMax = Math.max(0.005, slopes[Math.floor(slopes.length * 0.98)] || 0.005);
  d.seen = {}; d.seenYear = {};                // cell -> {year: n}; year -> cells with sightings
  d.avistamientos.celda.forEach(function (flat, i) {
    var y = d.avistamientos.anio[i];
    (d.seen[flat] = d.seen[flat] || {})[y] = d.avistamientos.n[i]; d.seenYear[y] = (d.seenYear[y] || 0) + 1;
  });
}

function status(t) { $("sp-status").textContent = t; $("sp-status").hidden = !t; }
function layerYears(d) { return state.layer === "chl" && d.idoneidad_chl ? d.idoneidad_chl.anios : d.anios; }
function layerValues(d, year) { var ys = layerYears(d), rows = state.layer === "chl" && d.idoneidad_chl ? d.idoneidad_chl.valores : d.idoneidad; return rows[ys.indexOf(year)]; }
function yearIn(d, y) { var ys = layerYears(d); return ys.indexOf(y) >= 0 ? y : y > ys[ys.length - 1] ? ys[ys.length - 1] : ys[0]; }
function trendClass(d, k) { var t = d.tendencia_celdas, b = t.slope_per_decade[k]; if (!has(b)) return -1; return has(t.p_adj[k]) && t.p_adj[k] < ALPHA ? (b > 0 ? 2 : 0) : 1; }
function nowClass(d, k) { var v = d.now[k]; return isNaN(v) ? -1 : v < d.umbral_idoneo ? 0 : v < 0.5 ? 1 : 2; }

/* ---------------- small charts ---------------- */
function fit(cv) {
  var d = window.devicePixelRatio || 1, w = cv.clientWidth, h = cv.clientHeight;
  cv.width = Math.round(w * d); cv.height = Math.round(h * d);
  var g = cv.getContext("2d"); g.setTransform(d, 0, 0, d, 0, 0); g.clearRect(0, 0, w, h); return { g: g, w: w, h: h };
}
function label(g, s, x, y, align, color) { g.font = "400 10px 'IBM Plex Mono',monospace"; g.fillStyle = color || css("--ink-2"); g.textAlign = align || "left"; g.textBaseline = "middle"; g.fillText(s, x, y); }
function seriesChart(cv, xs, ys, slopeDecade, decimals) {      // points, line and the Sen line
  var f = fit(cv), g = f.g; if (f.w < 60) return;
  var L = 52, R = 8, T = 6, B = 16, vals = ys.filter(has); if (!vals.length) return;
  var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals), pad = (hi - lo) * 0.12 || 1; lo -= pad; hi += pad;
  var x0 = xs[0], x1 = xs[xs.length - 1], X = function (x) { return L + (f.w - L - R) * (x - x0) / ((x1 - x0) || 1); }, Y = function (v) { return T + (f.h - T - B) * (hi - v) / (hi - lo); };
  [lo + pad, hi - pad].forEach(function (v) { g.beginPath(); g.moveTo(L, Y(v)); g.lineTo(f.w - R, Y(v)); g.strokeStyle = css("--rule"); g.lineWidth = 0.8; g.stroke(); label(g, v.toLocaleString("en-GB", { maximumFractionDigits: decimals }), L - 6, Y(v), "right"); });
  label(g, String(Math.round(x0)), L, f.h - 6, "left"); label(g, String(Math.round(x1)), f.w - R, f.h - 6, "right");
  var ink = css("--ink"), prev = false;
  g.beginPath(); ys.forEach(function (v, i) { if (!has(v)) { prev = false; return; } if (prev) g.lineTo(X(xs[i]), Y(v)); else g.moveTo(X(xs[i]), Y(v)); prev = true; });
  g.strokeStyle = ink; g.lineWidth = 1.5; g.lineJoin = "round"; g.stroke();
  ys.forEach(function (v, i) { if (!has(v)) return; g.beginPath(); g.arc(X(xs[i]), Y(v), 1.8, 0, 7); g.fillStyle = ink; g.fill(); });
  if (!has(slopeDecade)) return;
  var b = slopeDecade / 10, mid = (x0 + x1) / 2, rest = []; ys.forEach(function (v, i) { if (has(v)) rest.push(v - b * (xs[i] - mid)); });
  var c = median(rest);                         // Sen line: the slope, through the median of the points
  g.beginPath(); g.moveTo(X(x0), Y(c + b * (x0 - mid))); g.lineTo(X(x1), Y(c + b * (x1 - mid))); g.strokeStyle = css("--probe"); g.lineWidth = 2; g.setLineDash([6, 4]); g.stroke(); g.setLineDash([]);
}
function verdict(ind, decimals) {
  if (!has(ind.pendiente_decada)) return "Too few years with data for a trend.";
  var s = signed(ind.pendiente_decada, decimals) + " " + ind.unidad + " per decade";
  return (ind.significativa ? (ind.pendiente_decada > 0 ? "↑ " : "↓ ") + s + " · significant" : "No significant trend (" + s + ")") + " · " + pText(ind.p_adj) + " · n = " + ind.n;
}
function indicator(parent, ind, title, decimals, extra) {
  var div = el("div", "mini"), cv = document.createElement("canvas");
  div.appendChild(el("h3", "", title)); div.appendChild(cv); div.appendChild(el("p", "", verdict(ind, decimals)));
  if (extra) div.appendChild(el("p", "sp-extra", extra));
  parent.appendChild(div); seriesChart(cv, ind.anios, ind.serie, ind.pendiente_decada, decimals);
}

/* ---------------- panel ---------------- */
function fmt(x) { var a = Math.abs(x); return a >= 100 ? x.toFixed(0) : a >= 1 ? x.toFixed(2) : x.toPrecision(2); }

function panel() {
  var d = state.data, ind = d.indicadores, yrs = d.anios;
  $("sp-title").innerHTML = "<i>" + d.nombre + "</i>";
  $("sp-sub").textContent = [commonName(d), d.presencias.toLocaleString("en-GB") + " presences (cell and year) from " + d.registros.toLocaleString("en-GB") + " OBIS records",
    "seen from " + deg(d.latitud_presencias[0], "N", "S") + " to " + deg(d.latitud_presencias[1], "N", "S"), "updated " + new Date(d.calculado).toLocaleDateString()].filter(Boolean).join(" · ");
  $("sp-period").textContent = "Yearly results " + yrs[0] + " to " + yrs[yrs.length - 1] + " · " + d.region.cell_deg + "° cells";

  $("sp-area").textContent = verdict(ind.area, 0);
  var mid = yrs[Math.floor(yrs.length / 2)];
  $("sp-years").innerHTML = "<span>" + yrs[0] + "</span><span>" + mid + "</span><span>" + yrs[yrs.length - 1] + "</span>";

  var model = $("sp-model"); model.textContent = "";
  indicator(model, ind.centro, "Centre of the suitable habitat (latitude, °N)", 2);
  indicator(model, ind.limite, "Poleward limit: 95 % of the suitable area lies south of it (°N)", 2);

  var tol = $("sp-tol"), o = d.observado; tol.textContent = "";
  if (o.ventanas) {
    $("sp-tol-note").textContent = "The temperatures at the places where the species is seen, period by period (" + o.ventanas.length + " periods of " + o.anios_por_ventana + " year" + (o.anios_por_ventana > 1 ? "s" : "") +
      ", each cut to the same " + o.presencias_por_ventana + " presences). Each limit is compared with how fast the sea has warmed at those same places: a ratio near 1 means the species stayed and now lives in warmer water; " +
      "near 0, that it keeps to the same temperatures; above 1, that it is being seen in warmer places than before. Where people look for it also moves these numbers.";
    [["tolerancia_frio", "Cold limit (°C): coldest month, 5th percentile"], ["tolerancia_centro", "Centre (°C): mid-year temperature, median"], ["tolerancia_calor", "Warm limit (°C): hottest month, 95th percentile"]].forEach(function (t) {
      var i = ind[t[0]]; if (!i) return;
      indicator(tol, i, t[1], 2, "Sea at its places: " + signed(i.calentamiento_decada, 2) + " °C per decade" + (has(i.razon) ? " · ratio " + i.razon.toFixed(2) : ""));
    });
  } else $("sp-tol-note").textContent = o.motivo || "Not enough sightings spread over the years.";

  var obs = $("sp-obs"), any = false; obs.textContent = "";
  [["limite_observado_norte", "Northern Hemisphere (°N)"], ["limite_observado_sur", "Southern Hemisphere (°S)"]].forEach(function (t) {
    if (ind[t[0]]) { any = true; indicator(obs, ind[t[0]], t[1] + ": 95th percentile of the latitude of the sightings", 2, "Same " + ind[t[0]].presencias_por_ventana + " presences in every period."); }
  });
  $("sp-obs-box").hidden = !any;

  var lim = Object.keys(d.factores_limitantes_pct).sort(function (a, b) { return d.factores_limitantes_pct[b] - d.factores_limitantes_pct[a]; });
  $("sp-limits").innerHTML = lim.length ? lim.map(function (v) {
    var p = d.factores_limitantes_pct[v];
    return '<li><span>' + varName(v) + '</span><span class="pct">' + Math.round(p) + '%</span><span class="bar" style="width:' + p + '%"></span></li>';
  }).join("") : '<li><span class="dim">Suitable everywhere with the data available.</span></li>';

  $("sp-niche").innerHTML = d.variables.map(function (v) {
    var n = d.nicho[v.id];
    return "<tr><td>" + v.name + (v.en_indice ? " *" : "") + '</td><td class="num">' + fmt(n.p5) + " – " + fmt(n.p95) + " " + v.unit + '</td><td class="num">' + fmt(n.p50) + "</td></tr>";
  }).join("");

  var tests = Object.keys(ind).length;
  $("sp-method").textContent = "Trends: Sen's slope (median of the slopes between every pair of years) and the modified Mann-Kendall test, which allows for one year resembling the next. " +
    "The p-values of the " + tests + " indicators of this species are adjusted together (Benjamini-Hochberg), and so are those of the " + d.ocean_index.length.toLocaleString("en-GB") + " cells of the trend map. Significant means adjusted p < " + ALPHA + ".";
  $("sp-layer-box").hidden = !d.idoneidad_chl;
  world(); bars();
}
function varName(id) { var v = state.data.variables.filter(function (x) { return x.id === id; })[0]; return v ? v.name : id; }

function bars() {
  if (!state.data) return;
  var a = state.data.indicadores.area, years = a.anios, max = Math.max.apply(null, a.serie.filter(has).concat([1])), yearShown = !flatKind() || flatKind() === "suit";
  var box = $("sp-bars"); box.innerHTML = ""; box.style.gridTemplateColumns = "repeat(" + years.length + ",1fr)";
  years.forEach(function (y, i) {
    var v = a.serie[i] || 0, b = el("div"); b.style.height = Math.max(2, 100 * v / max) + "%";
    b.title = y + ": " + Math.round(v).toLocaleString("en-GB") + " km² suitable (" + a.pct_del_mar[i] + " % of the sea)"; if (y === state.year && yearShown) b.className = "on";
    b.addEventListener("click", function () { state.screen = "viz"; state.mode = "time"; state.layer = "temp"; state.focus = true; spModes.hidden = false; setYear(y); });
    box.appendChild(b);
  });
}

function world() {                               // where the profile comes from: sightings in 2° cells
  var d = state.data, cv = $("sp-world"), f = fit(cv), g = f.g; if (f.w < 40) return;
  g.fillStyle = css("--paper"); g.fillRect(0, 0, f.w, f.h);
  var X = function (lon) { return (lon + 180) / 360 * f.w; }, Y = function (lat) { return (90 - lat) / 180 * f.h; }, r = d.region;
  g.fillStyle = css("--probe");
  d.mundo.forEach(function (p) { g.globalAlpha = Math.min(1, 0.45 + Math.log10(p[2] + 1) * 0.3); g.fillRect(X(p[1] - 1), Y(p[0] + 1), Math.max(1.5, f.w / 180), Math.max(1.5, f.h / 90)); });
  g.globalAlpha = 1; g.strokeStyle = css("--ink"); g.lineWidth = 1;
  g.strokeRect(X(r.lon_west), Y(r.lat_north), r.nx * r.cell_deg / 360 * f.w, r.ny * r.cell_deg / 180 * f.h);
  g.strokeStyle = css("--rule"); g.beginPath(); g.moveTo(0, Y(0)); g.lineTo(f.w, Y(0)); g.stroke(); g.strokeRect(0.5, 0.5, f.w - 1, f.h - 1);
  $("sp-world-note").textContent = "Each mark is a 2° cell with sightings used for the profile. The box is the Mediterranean map. " +
    (d.registros_sin_anio ? d.registros_sin_anio.toLocaleString("en-GB") + " records without a year in range and " : "") + d.registros_en_tierra.toLocaleString("en-GB") + " records far from the sea were left out.";
}

function setYear(y) {
  var ys = state.data ? layerYears(state.data) : [y];
  state.year = Math.max(ys[0], Math.min(ys[ys.length - 1], y)); refresh();
}

/* ---------------- combined change (Analysis) ---------------- */
// Two things per cell, three classes each:
//  - habitat: the species' suitability trend (falling / no significant trend / rising, adjusted p < ALPHA);
//  - sea: how far the summer maximum, winter minimum, heatwave months and chlorophyll have moved over the
//    period, each in its own normal year-to-year swing (total linear change ÷ standard deviation around the
//    line), then the mean of the four sizes. Classes are thirds of the cells of the map.
var envChange = null;
function shift(v) {                      // total linear change of a yearly series, in standard deviations around its line
  var x = [], y = [], i, n, mx = 0, my = 0, sxy = 0, sxx = 0, ss = 0;
  v.forEach(function (a, k) { if (has(a)) { x.push(k); y.push(a); } });
  n = x.length; if (n < 5) return NaN;
  for (i = 0; i < n; i++) { mx += x[i] / n; my += y[i] / n; }
  for (i = 0; i < n; i++) { sxy += (x[i] - mx) * (y[i] - my); sxx += (x[i] - mx) * (x[i] - mx); }
  var b = sxy / sxx; for (i = 0; i < n; i++) ss += Math.pow(y[i] - my - b * (x[i] - mx), 2);
  var sd = Math.sqrt(ss / (n - 2)); return sd > 0 ? b * (x[n - 1] - x[0]) / sd : 0;
}
function envFor() {                      // promise: {cells: "lat,lon" -> {z: [four shifts], index}, years, step}
  if (!envChange) {
    envChange = api("/trends/" + ENV_SET + "/meta.json").then(function (meta) {
      return Promise.all(ENV_VARS.map(function (v) { return api("/trends/" + ENV_SET + "/" + v[0] + ".json"); })).then(function (files) {
        var r = meta.region, cells = {};
        meta.ocean_index.forEach(function (f, k) {
          var lat = r.lat_north - (Math.floor(f / r.nx) + 0.5) * r.cell_deg, lon = r.lon_west + (f % r.nx + 0.5) * r.cell_deg;
          var z = files.map(function (d) { return shift(d.values.map(function (rowv) { return rowv[k]; })); }), ok = z.filter(has);
          cells[lat + "," + lon] = { z: z, index: ok.length ? ok.reduce(function (a, x) { return a + Math.abs(x); }, 0) / ok.length : NaN };
        });
        return { cells: cells, years: meta.years, step: r.cell_deg };
      });
    });
    envChange.catch(function () { envChange = null; });
  }
  return envChange;
}
function bivFor(d) {                     // per cell classes of the combined change; null while it loads
  if (d.biv) return d.biv.hab ? d.biv : null;
  d.biv = {};
  envFor().then(function (env) {
    var r = d.region, n = d.ocean_index.length, hab = new Int8Array(n).fill(-1), sea = new Int8Array(n).fill(-1), cell = [], idx = [], st = env.step;
    d.ocean_index.forEach(function (fl, k) {   // each 0.25° species cell reads the 0.5° cell that contains it
      var lat = r.lat_north - (Math.floor(fl / r.nx) + 0.5) * r.cell_deg, lon = r.lon_west + (fl % r.nx + 0.5) * r.cell_deg;
      var c = env.cells[(Math.floor(lat / st) * st + st / 2) + "," + (Math.floor(lon / st) * st + st / 2)], t = trendClass(d, k);
      if (!c || !has(c.index) || t < 0) return;
      cell[k] = c; hab[k] = t; idx.push(c.index);
    });
    var i1 = quantile(idx, 1 / 3), i2 = quantile(idx, 2 / 3);
    cell.forEach(function (c, k) { if (c) sea[k] = c.index < i1 ? 0 : c.index < i2 ? 1 : 2; });
    d.biv = { hab: hab, sea: sea, cell: cell, cuts: [i1, i2], period: [env.years[0], env.years[env.years.length - 1]] };
    refresh();
  }).catch(function (e) { delete d.biv; status("Analysis not available: " + e.message); });
  return null;
}

/* ---------------- legend, bottom bar ---------------- */
function flatKind() {                    // null: the stack; else what the flat map shows
  return state.screen === "analysis" ? "biv" : state.mode === "trend" ? "trend" : state.mode === "bi" ? "bi" : state.focus ? "suit" : null;
}
function refresh() {
  if (state.page !== "species") return;
  press(spScreens, state.screen); press(spModes, state.mode); spModes.hidden = state.screen === "analysis";
  var d = state.data, kind = flatKind();
  if (!d) { draw(); return; }
  var years = layerYears(d), sel = $("sp-year");
  if (years.indexOf(state.year) < 0) state.year = years[years.length - 1];
  sel.innerHTML = years.slice().reverse().map(function (y) { return '<option value="' + y + '">' + y + " · seen in " + (d.seenYear[y] || 0) + " cells</option>"; }).join("");
  sel.value = state.year;
  document.querySelectorAll("#sp-layer button").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.layer === state.layer)); });
  $("sp-layer-note").textContent = state.layer === "chl" ? "Temperature and chlorophyll together, " + years[0] + " to " + years[years.length - 1] + ". Trend maps always use the temperature index."
    : "Coldest month, hottest month and unusually hot months, " + d.anios[0] + " to " + d.anios[d.anios.length - 1] + ".";
  var L = $("sp-legend"), land = '<span><span class="sw land"></span>land</span>';
  if (kind === "biv") {
    var B = d.biv && d.biv.hab ? d.biv : null, rows = ["gaining", "stable", "losing"].map(function (t, r) {
      return "<tr><th>" + t + "</th>" + BIV[2 - r].map(function (c) { return '<td style="background:' + c + '"></td>'; }).join("") + "</tr>";
    }).join("");
    L.innerHTML = '<table class="sp-biv"><caption>Habitat suitability ↓ &nbsp;·&nbsp; Sea change →</caption>' + rows +
      "<tr><th></th><td>low</td><td>medium</td><td>high</td></tr></table>" +
      '<span class="sp-biv-note">Habitat: trend of suitability since ' + d.anios[0] + ', significant when adjusted p &lt; ' + ALPHA + '. Sea change: how far the summer maximum and winter minimum sea temperature, ' +
      'marine heatwave months and chlorophyll have moved' + (B ? " in " + B.period[0] + "–" + B.period[1] : "") + ", counted in their normal year-to-year swings (mean of the four)." +
      (B ? " Cuts: " + B.cuts[0].toFixed(1) + " and " + B.cuts[1].toFixed(1) + " swings, thirds of this map." : "") + "</span>" + land;
  } else if (kind === "trend") L.innerHTML = '<span>Suitability falling<span class="ramp"></span>rising</span><span>range ±' + d.slopeMax.toFixed(2) + ' per decade, ' + d.anios[0] + '–' + d.anios[d.anios.length - 1] +
    '</span><span><span class="sw hatch"></span>not significant (adjusted p ≥ ' + ALPHA + ')</span>' + land;
  else if (kind === "bi") {
    var grid = '<span class="bi">'; [2, 1, 0].forEach(function (n) { [0, 1, 2].forEach(function (t) { grid += '<i style="background:' + (BI[n][t] || css("--mid")) + '" title="' + NOW_WORD[n] + ", " + TREND_WORD[t] + '"></i>'; }); });
    L.innerHTML = grid + '</span><span>rows, bottom to top: suitability of the last ' + NOW_YEARS + ' years below ' + d.umbral_idoneo + ' · up to 0.5 · above<br>columns, left to right: suitability falling · no significant trend · rising, since ' + d.anios[0] + '</span>' + land;
  } else L.innerHTML = '<span>Habitat suitability: 0 <span class="ramp sp"></span> 1</span>' +
    (kind ? '<span><span class="sp-dot"></span>seen in ' + state.year + '</span><span><span class="sp-dot faint"></span>seen in other years</span>' : "") + land;
  var yearBar = !kind || kind === "suit";
  $("sp-back").hidden = kind !== "suit";
  ["sp-prev", "sp-year", "sp-next"].forEach(function (id) { $(id).hidden = !yearBar; });
  $("sp-prev").disabled = state.year <= years[0]; $("sp-next").disabled = state.year >= years[years.length - 1];
  $("sp-hint").textContent = kind ? "Point at the map to read a place." : "Click a map to open it.";
  bars(); draw();
}

/* ---------------- maps ---------------- */
var canvas = $("sp-map"), geom = null, hits = [], tip = $("tip"), images = {};
function cellColor(d, k, kind, values) {
  var midc = rgb(css("--mid"));
  if (kind === "suit") { var v = values[k]; return v === null ? null : mix(midc, rgb(css("--neg")), Math.pow(v / 100, 0.9)); }
  if (kind === "trend") { var b = d.tendencia_celdas.slope_per_decade[k]; if (!has(b)) return null; var t = Math.max(-1, Math.min(1, b / d.slopeMax)); return mix(midc, rgb(css(t < 0 ? "--neg" : "--pos")), Math.pow(Math.abs(t), 0.8)); }
  if (kind === "biv") { var B = d.biv; if (!B || !B.hab || B.hab[k] < 0 || B.sea[k] < 0) return null; return rgb(BIV[B.hab[k]][B.sea[k]]); }
  var n = nowClass(d, k), c = trendClass(d, k); if (n < 0 || c < 0) return null; return rgb(BI[n][c] || css("--mid"));
}
function mapImage(d, kind, values) {     // one pixel per cell; drawn scaled up without smoothing
  var r = d.region, img = document.createElement("canvas"); img.width = r.nx; img.height = r.ny;
  var ig = img.getContext("2d"), im = ig.createImageData(r.nx, r.ny), land = rgb(css("--land")), nodata = rgb(css("--rule"));
  for (var fl = 0; fl < r.nx * r.ny; fl++) { var k = d.cellOf[fl], c = k < 0 ? land : (cellColor(d, k, kind, values) || nodata), o = fl * 4; im.data[o] = c[0]; im.data[o + 1] = c[1]; im.data[o + 2] = c[2]; im.data[o + 3] = k < 0 ? 150 : 255; }
  ig.putImageData(im, 0, 0); return img;
}
function stackImage(d, y) {              // a stack layer painted once, then reused while nothing changes
  var key = [d.nombre, y, state.layer, css("--mid")].join("|");
  if (!images[key]) { if (Object.keys(images).length > 48) images = {}; images[key] = mapImage(d, "suit", layerValues(d, y)); }
  return images[key];
}
function hatchPattern(g) {
  var cv = document.createElement("canvas"); cv.width = cv.height = 6; var h = cv.getContext("2d");
  h.strokeStyle = css("--ink"); h.globalAlpha = 0.5; h.lineWidth = 1; h.beginPath(); h.moveTo(-1, 7); h.lineTo(7, -1); h.stroke();
  return g.createPattern(cv, "repeat");
}

function layers() {                      // what the stack shows, top layer first (four at most)
  var d = state.data;
  if (state.mode !== "species") {        // the chosen year on top, the three years before it below
    var ys = layerYears(d), i = Math.max(ys.indexOf(state.year), Math.min(3, ys.length - 1));
    return ys.slice(Math.max(0, i - 3), i + 1).reverse().map(function (y) { return { name: d.nombre, year: y, label: String(y), sel: y === state.year }; });
  }
  var L = state.list, j = 0; L.forEach(function (sp, k) { if (sp.nombre === d.nombre) j = k; });
  var a = Math.max(0, Math.min(j - 1, L.length - 4));
  return L.slice(a, a + 4).map(function (sp) { return { name: sp.nombre, year: state.year, label: sp.nombre, sel: sp.nombre === d.nombre, italic: true }; });
}

function draw() {
  if (state.page !== "species") return;
  var f = fit(canvas), g = f.g, w = f.w, h = f.h; geom = null; hits = [];
  var d = state.data; if (!w || !h || !d) return;
  var kind = flatKind(); canvas.style.cursor = kind ? "crosshair" : "pointer";
  if (kind) drawFlat(g, w, h, d, kind); else drawStack(g, w, h, d);
}

function drawStack(g, w, h, d) {         // every layer is a flat map slanted with the ISO vectors; lower layers first
  var Ls = layers(), r = d.region, gap = h / 4.6, unitW = r.nx * ISO.ux - r.ny * ISO.vx, missing = 0, i;
  var s = Math.max(0.3, Math.min((w - 28) / unitW, (gap - 46) / (r.ny * ISO.vy)));
  var ox = (w - unitW * s) / 2 - r.ny * ISO.vx * s, fw = r.nx * s, fh = r.ny * s;
  function at(a, b, top) { return [ox + a * ISO.ux + b * ISO.vx, top + a * ISO.uy + b * ISO.vy]; }
  function tag(t, x, y, o) { g.fillStyle = css(o.color || "--ink-2"); g.textAlign = o.align || "left"; g.textBaseline = "middle";
    g.font = (o.italic ? "italic " : "") + (o.weight || "400") + " " + o.size + "px " + css(o.mono ? "--mono" : "--body"); g.fillText(t, x, y); }
  for (i = Ls.length - 1; i >= 0; i--) {
    var L = Ls[i], D = L.name === d.nombre ? d : get(L.name), top = 0.5 * gap - 4 + i * gap, y = D ? yearIn(D, L.year) : L.year;
    g.save(); g.transform(ISO.ux, ISO.uy, ISO.vx, ISO.vy, ox, top); g.imageSmoothingEnabled = false;
    if (D) g.drawImage(stackImage(D, y), 0, 0, fw, fh); else { missing++; g.fillStyle = css("--land"); g.fillRect(0, 0, fw, fh); }
    g.restore();
    var c = [at(0, 0, top), at(fw, 0, top), at(fw, fh, top), at(0, fh, top)];
    g.beginPath(); c.forEach(function (p, k) { if (k) g.lineTo(p[0], p[1]); else g.moveTo(p[0], p[1]); }); g.closePath();
    g.strokeStyle = css(L.sel ? "--ink" : "--ink-2"); g.lineWidth = L.sel ? 2 : 0.8; g.stroke();
    tag(L.label, c[3][0], c[3][1] + 30, { color: "--ink", weight: L.sel ? "500" : "400", size: 13, italic: L.italic });
    tag(D ? "seen in " + (D.seenYear[y] || 0) + " cells in " + y : "loading…", c[1][0], c[1][1] - 30, { align: "right", size: 11.5, mono: true });
    hits[i] = { L: L, top: top, fw: fw, fh: fh, ox: ox };
  }
  status(missing ? "Loading species…" : "");
}

function drawFlat(g, w, h, d, kind) {    // one map with degree labels
  var r = d.region, nx = r.nx, ny = r.ny, s = Math.min((w - 90) / nx, (h - 80) / ny), ox = (w - nx * s) / 2 + 10, oy = (h - ny * s) / 2;
  geom = { s: s, ox: ox, oy: oy };
  if (kind === "biv" && !bivFor(d)) status("Computing the combined change…"); else status("");
  g.imageSmoothingEnabled = false; g.drawImage(mapImage(d, kind, kind === "suit" ? layerValues(d, state.year) : null), ox, oy, nx * s, ny * s);
  if (kind === "trend") {
    var t = d.tendencia_celdas; g.fillStyle = hatchPattern(g);
    d.ocean_index.forEach(function (flat, k2) { if (has(t.slope_per_decade[k2]) && !(t.p_adj[k2] < ALPHA)) g.fillRect(ox + (flat % nx) * s, oy + Math.floor(flat / nx) * s, s + 0.5, s + 0.5); });
  }
  if (kind === "suit") {                   // sightings: other years small and faint, the chosen year on top
    var X = function (flat) { return ox + (flat % nx + 0.5) * s; }, Y = function (flat) { return oy + (Math.floor(flat / nx) + 0.5) * s; };
    g.fillStyle = css("--probe"); g.globalAlpha = 0.3;
    Object.keys(d.seen).forEach(function (flat) { if (!d.seen[flat][state.year]) { g.beginPath(); g.arc(X(+flat), Y(+flat), 1.5, 0, 7); g.fill(); } });
    g.globalAlpha = 1; g.strokeStyle = css("--sheet"); g.lineWidth = 1;
    Object.keys(d.seen).forEach(function (flat) { var n = d.seen[flat][state.year]; if (n) { g.beginPath(); g.arc(X(+flat), Y(+flat), Math.min(6, 3 + Math.log10(n) * 1.5), 0, 7); g.fill(); g.stroke(); } });
  }
  g.strokeStyle = css("--ink-2"); g.lineWidth = 0.8; g.strokeRect(ox, oy, nx * s, ny * s);
  for (var lon = Math.ceil(r.lon_west / 5) * 5; lon <= r.lon_west + nx * r.cell_deg; lon += 5) label(g, Math.abs(lon) + "°" + (lon >= 0 ? "E" : "W"), ox + (lon - r.lon_west) / r.cell_deg * s, oy + ny * s + 12, "center");
  for (var lat = Math.floor(r.lat_north / 5) * 5; lat >= r.lat_north - ny * r.cell_deg; lat -= 5) label(g, lat + "°N", ox - 6, oy + (r.lat_north - lat) / r.cell_deg * s, "right");
  var title = kind === "suit" ? state.year + " · habitat suitability" + (state.layer === "chl" ? " (temperature and chlorophyll)" : "") : kind === "trend" ? "Trend of habitat suitability per decade"
    : kind === "bi" ? "Suitability now × its trend" : "Habitat trend × how much the sea has changed";
  g.font = "500 13px 'IBM Plex Mono',monospace"; g.fillStyle = css("--ink"); g.textAlign = "left"; g.textBaseline = "middle"; g.fillText(title, ox, oy - 14);
  if (kind === "suit" && state.layer === "temp") {   // line of the poleward limit of the year
    var lim = d.indicadores.limite, v = lim.serie[lim.anios.indexOf(state.year)];
    if (has(v)) { var py = oy + (r.lat_north - v) / r.cell_deg * s;
      g.beginPath(); g.moveTo(ox, py); g.lineTo(ox + nx * s, py); g.strokeStyle = css("--probe"); g.lineWidth = 1.2; g.setLineDash([5, 4]); g.stroke(); g.setLineDash([]);
      g.font = "400 10px 'IBM Plex Mono',monospace"; g.fillStyle = css("--ink"); g.textAlign = "right"; g.fillText("poleward limit " + v.toFixed(1) + "°N", ox + nx * s - 4, py - 7); }
  }
}

canvas.addEventListener("click", function (ev) {     // stack: open the layer under the pointer
  var r = canvas.getBoundingClientRect(), px = ev.clientX - r.left, py = ev.clientY - r.top, det = ISO.ux * ISO.vy - ISO.vx * ISO.uy, i, H, a, b;
  for (i = 0; i < hits.length; i++) {                // top layer first: it is the one in front
    H = hits[i]; if (!H) continue;
    a = ((px - H.ox) * ISO.vy - ISO.vx * (py - H.top)) / det; b = (ISO.ux * (py - H.top) - ISO.uy * (px - H.ox)) / det;
    if (a < 0 || a > H.fw || b < 0 || b > H.fh) continue;
    state.focus = true;
    if (H.L.name !== state.species) show(H.L.name); else setYear(H.L.year);
    return;
  }
});

canvas.addEventListener("mousemove", function (ev) {
  var d = state.data, kind = flatKind(); if (!d || !geom || !kind) return;
  var rc = canvas.getBoundingClientRect(), r = d.region, i = Math.floor((ev.clientX - rc.left - geom.ox) / geom.s), j = Math.floor((ev.clientY - rc.top - geom.oy) / geom.s);
  if (i < 0 || i >= r.nx || j < 0 || j >= r.ny) { tip.style.opacity = 0; return; }
  var flat = j * r.nx + i, k = d.cellOf[flat], lat = r.lat_north - (j + 0.5) * r.cell_deg, lon = r.lon_west + (i + 0.5) * r.cell_deg;
  var where = deg(lat, "N", "S") + ", " + deg(lon, "E", "W"), t, tc = d.tendencia_celdas;
  if (k < 0) t = where + " · land";
  else if (kind === "biv") {
    var B = d.biv && d.biv.hab ? d.biv : null, c = B && B.cell[k];
    t = where + (c ? " · habitat " + ["losing", "stable", "gaining"][B.hab[k]] + " (" + signed(tc.slope_per_decade[k], 3) + " per decade) · sea change " + ["low", "medium", "high"][B.sea[k]] +
      " (" + c.index.toFixed(1) + " swings: " + ENV_VARS.map(function (v, n) { return v[1] + " " + (has(c.z[n]) ? signed(c.z[n], 1) : "–"); }).join(", ") + ")" : " · no data");
  } else {
    var v = layerValues(d, state.year)[k], li = state.layer === "temp" ? d.limitante[d.anios.indexOf(state.year)][k] : null, seen = d.seen[flat];
    t = where + " · suitability " + (v === null ? "–" : (v / 100).toFixed(2)) + " in " + state.year;
    if (v !== null && v / 100 < d.umbral_idoneo && li !== null && li >= 0) t += " · limited by " + varName(d.limitantes[li]).toLowerCase();
    if (has(tc.slope_per_decade[k])) t += " · trend " + signed(tc.slope_per_decade[k], 3) + " per decade, " + (tc.p_adj[k] < ALPHA ? "significant" : "not significant");
    if (seen) t += " · seen here in " + Object.keys(seen).length + " year" + (Object.keys(seen).length > 1 ? "s" : "") + (seen[state.year] ? ", " + state.year + " included" : "");
  }
  $("sp-cell").textContent = t;
  tip.textContent = t.split(" · ").slice(0, 2).join(" · "); tip.style.left = Math.min(ev.clientX, window.innerWidth - 360) + "px"; tip.style.top = ev.clientY + "px"; tip.style.opacity = 1;
});
canvas.addEventListener("mouseleave", function () { tip.style.opacity = 0; });

/* ---------------- search, year, layer, start ---------------- */
// Autocomplete: as you type, the backend asks OBIS for species names (most recorded first).
var find = $("sp-find"), suggest = $("sp-suggest"), timer, request = null, results = [], active = -1;

function closeSuggest() {
  suggest.hidden = true; find.setAttribute("aria-expanded", "false"); find.removeAttribute("aria-activedescendant"); active = -1;
}
function esc(t) { return String(t).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
function renderSuggest(q, message) {
  if (message) { suggest.innerHTML = '<li class="sp-note">' + esc(message) + "</li>"; }
  else {
    var n = q.length;
    suggest.innerHTML = results.map(function (r, i) {
      var name = r.nombre.toLowerCase().indexOf(q.toLowerCase()) === 0
        ? "<b>" + esc(r.nombre.slice(0, n)) + "</b>" + esc(r.nombre.slice(n)) : esc(r.nombre);
      var info = [r.familia, r.clase].filter(Boolean).join(" · ");
      return '<li role="option" id="sp-opt-' + i + '" aria-selected="' + (i === active) + '" data-i="' + i + '">' +
        '<i>' + name + "</i><small>" + esc(info) + (info ? " · " : "") + (r.registros || 0).toLocaleString() + " records</small></li>";
    }).join("");
  }
  suggest.hidden = false; find.setAttribute("aria-expanded", "true");
  if (active >= 0) find.setAttribute("aria-activedescendant", "sp-opt-" + active); else find.removeAttribute("aria-activedescendant");
}
function choose(i) {
  var r = results[i]; if (!r) return;
  find.value = r.nombre; closeSuggest(); show(r.nombre);
}

find.addEventListener("input", function () {
  clearTimeout(timer);
  var q = find.value.trim();
  if (request) request.abort();
  if (q.length < 3) { results = []; closeSuggest(); return; }
  timer = setTimeout(function () {
    request = new AbortController();
    renderSuggest(q, "Searching OBIS…");
    fetch(API + "/especies/buscar?q=" + encodeURIComponent(q), { signal: request.signal })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (res) {
        if (find.value.trim() !== q) return;          // the user kept typing
        results = res; active = res.length ? 0 : -1;
        renderSuggest(q, res.length ? null : "No species with OBIS records match “" + q + "”.");
      })
      .catch(function (e) { if (e.name !== "AbortError") renderSuggest(q, "Search not available right now."); });
  }, 250);
});
find.addEventListener("keydown", function (ev) {
  var open = !suggest.hidden && results.length;
  if (ev.key === "ArrowDown" && open) { active = (active + 1) % results.length; renderSuggest(find.value.trim()); ev.preventDefault(); }
  else if (ev.key === "ArrowUp" && open) { active = (active - 1 + results.length) % results.length; renderSuggest(find.value.trim()); ev.preventDefault(); }
  else if (ev.key === "Enter") {
    ev.preventDefault();
    if (open && active >= 0) choose(active);
    else if (find.value.trim().split(/\s+/).length >= 2) { var q = find.value.trim(); closeSuggest(); show(q.charAt(0).toUpperCase() + q.slice(1).toLowerCase()); }
  }
  else if (ev.key === "Escape") closeSuggest();
});
// mousedown, not click: it runs before the input loses focus and the list closes.
suggest.addEventListener("mousedown", function (ev) {
  var li = ev.target.closest("li[data-i]"); if (!li) return;
  ev.preventDefault(); choose(Number(li.dataset.i));
});
find.addEventListener("blur", function () { setTimeout(closeSuggest, 100); });
find.addEventListener("focus", function () { if (results.length && find.value.trim().length >= 3) renderSuggest(find.value.trim()); });
$("sp-year").addEventListener("change", function (ev) { setYear(Number(ev.target.value)); });
$("sp-prev").addEventListener("click", function () { setYear(state.year - 1); });
$("sp-next").addEventListener("click", function () { setYear(state.year + 1); });
$("sp-back").addEventListener("click", function () { state.focus = false; refresh(); });
document.querySelectorAll("#sp-layer button").forEach(function (b) { b.addEventListener("click", function () {
  state.layer = b.dataset.layer; if (state.mode === "trend" || state.mode === "bi") state.mode = "time"; refresh();
}); });

function start() {
  var wanted = new URLSearchParams(location.search).get("especie");
  loadList().then(function (list) {
    var first = wanted ? { nombre: wanted } : list.filter(function (sp) { return sp.nombre === "Octopus vulgaris"; })[0] || list[0];
    if (first) show(first.nombre);
    else status("No species stored yet. Type a scientific name on the left.");
  }).catch(function (e) { status("The species service is not available right now (" + e.message + ")."); });
}

/* ---------------- Variables page: choose the data set (region + period) ---------------- */
// The Variables screen (app.js) reads one set at a time; changing it reloads the page with ?datos=<set>.
(function dataSetPicker() {
  var place = $("region-name"); if (!place) return;
  var current = new URLSearchParams(location.search).get("datos") || "mediterraneo-1982";
  var sel = el("select"); sel.id = "data-set"; sel.setAttribute("aria-label", "Region and period");
  sel.className = "sp-select";
  place.parentNode.insertBefore(sel, place.nextSibling);
  api("/trends").then(function (sets) {
    sel.innerHTML = sets.map(function (d) {
      return '<option value="' + d.id + '">' + d.region + ", " + d.desde + "–" + d.hasta + " (" + d.variables.length +
             (d.id.indexOf("habitat") === 0 ? " species variables, " : " variables, ") + d.cell_deg + "° cells)</option>";
    }).join("") + '<option value="ficheros">Mediterranean Sea, 2003–2025 (static files in data/, 0.5° cells)</option>';
    sel.value = current === "sintetico" ? "ficheros" : current;
  }).catch(function () {
    sel.innerHTML = '<option value="ficheros">Mediterranean Sea, 2003–2025 (static files in data/)</option>'; sel.value = "ficheros";
  });
  sel.addEventListener("change", function () {
    var q = new URLSearchParams(location.search); q.set("datos", sel.value);
    location.search = q.toString();
  });
})();

function redraw() { if (state.page === "species" && state.data) { images = {}; draw(); panelCharts(); } }
var chartRaf = 0;
function panelCharts() { if (!chartRaf) chartRaf = requestAnimationFrame(function () { chartRaf = 0; if (state.data) panel(); }); }
if (window.ResizeObserver) { new ResizeObserver(draw).observe(row.querySelector(".stage")); new ResizeObserver(function () { if (state.page === "species" && state.data) panelCharts(); }).observe(row.querySelector(".panel")); }
if (window.matchMedia) window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", redraw);
if (document.fonts && document.fonts.ready) document.fonts.ready.then(redraw);
if (location.hash === "#species") setPage("species");
window.addEventListener("hashchange", function () { setPage(location.hash === "#species" ? "species" : "trends"); });
})();
