/* Species portal for Trend Detective. Self-contained: it adds the "Trends | Species" switch and its own
   sidebar, map and panel, and leaves app.js untouched. All computing happens in the Python backend
   (backend/especies_habitat.py): this file asks for one JSON per species and draws it.

   What the backend sends for a species (GET /api/habitat/especies/<name>):
     - its environmental profile: percentiles of the four chosen variables at its sightings worldwide;
     - habitat suitability (0 to 100) of every 0.25° Mediterranean sea cell, every year since 1980;
     - yearly indicators with their trend: suitable area, centre and poleward limit of the habitat,
       observed poleward limit, thermal tolerance. Same method as the rest of the site: Sen's slope,
       modified Mann-Kendall test, FDR adjustment.
   A species that is not stored yet is computed on its first request (its sightings come from OBIS). */
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

var $ = function (id) { return document.getElementById(id); };
function el(tag, cls, txt) { var e = document.createElement(tag); if (cls) e.className = cls; if (txt !== undefined) e.textContent = txt; return e; }
function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
function has(x) { return x !== null && x !== undefined && isFinite(x); }
function commonName(sp) { return COMMON_NAMES[sp.nombre] || sp.nombre_comun || ""; }
function rgb(h) { h = h.replace("#", ""); if (h.length === 3) h = h.replace(/./g, "$&$&"); return [0, 2, 4].map(function (i) { return parseInt(h.substr(i, 2), 16); }); }
function mix(a, b, t) { return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]; }
function median(a) { a = a.slice().sort(function (x, y) { return x - y; }); var h = a.length >> 1; return a.length % 2 ? a[h] : (a[h - 1] + a[h]) / 2; }
function signed(x, d) { return (x > 0 ? "+" : x < 0 ? "−" : "") + Math.abs(x).toLocaleString("en-GB", { minimumFractionDigits: d, maximumFractionDigits: d }); }
function pText(p) { return p < 0.001 ? "adjusted p < 0.001" : "adjusted p = " + p.toFixed(3); }
function deg(v, pos, neg) { return Math.abs(v).toFixed(2) + "°" + (v >= 0 ? pos : neg); }

var state = { page: "trends", species: null, year: null, mode: "suit", layer: "temp", data: null, list: [], cellOf: null, now: null, slopeMax: 1 };

/* ---------------- DOM: page switch, sidebar, main view, panel ---------------- */
var options = document.querySelector(".options");
var pages = el("span", "seg"); pages.id = "pages"; pages.setAttribute("role", "group"); pages.setAttribute("aria-label", "Portal");
[["trends", "Variables"], ["species", "Species"]].forEach(function (p) {
  var b = el("button", "", p[1]); b.type = "button"; b.dataset.page = p[0]; b.setAttribute("aria-pressed", String(p[0] === "trends"));
  b.addEventListener("click", function () { setPage(p[0]); }); pages.appendChild(b);
});
options.insertBefore(pages, options.firstChild);

var spModes = el("span", "seg"); spModes.id = "sp-modes"; spModes.hidden = true; spModes.setAttribute("role", "group"); spModes.setAttribute("aria-label", "Map");
[["suit", "Suitability by year"], ["trend", "Trend map"], ["bi", "Now × trend"]].forEach(function (m) {
  var b = el("button", "", m[1]); b.type = "button"; b.dataset.mode = m[0]; b.setAttribute("aria-pressed", String(m[0] === "suit"));
  b.addEventListener("click", function () { state.mode = m[0]; refresh(); }); spModes.appendChild(b);
});
options.insertBefore(spModes, $("modes").nextSibling);

var trendSide = document.querySelector(".side .side-body");
var side = el("div", "side-body"); side.id = "sp-side"; side.hidden = true;
side.innerHTML =
  '<section><h2>Region</h2><p class="fact">Mediterranean Sea</p><p class="fact dim" id="sp-period">&nbsp;</p></section>' +
  '<section><h2>Species</h2><p class="fact dim">Stored species. Cephalopods are always ready.</p><ul class="varlist" id="sp-list"></ul></section>' +
  '<section class="sp-search"><h2>Find another species</h2>' +
  '<input id="sp-find" list="sp-suggest" placeholder="Scientific name, e.g. Thalassoma pavo" autocomplete="off" aria-label="Scientific name">' +
  '<datalist id="sp-suggest"></datalist><p class="fact dim">Computed on first request, then stored.</p></section>' +
  '<section class="sp-month" id="sp-year-box"><h2>Year</h2><p class="fact" id="sp-year-name">&nbsp;</p>' +
  '<input type="range" id="sp-year" min="1980" max="2025" step="1" aria-label="Year"></section>' +
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
  '<div class="stage"><canvas id="sp-map" aria-label="Habitat suitability map of the Mediterranean"></canvas><p class="loading" id="sp-status">Loading…</p></div></main>' +
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
  'chlorophyll starts in 2003 (satellite) and is a separate layer. <a id="sp-vars-link" href="?datos=habitat-1980">See the trends of these variables</a>.</p></section>' +
  '<section><h2>Where it is seen in the world</h2><canvas id="sp-world" aria-label="World map of the sightings used"></canvas><p class="note" id="sp-world-note"></p></section>' +
  '<section><h2>Place</h2><p class="fact dim sp-cell" id="sp-cell">Point at the map to read a place.</p></section>' +
  '<section><p class="note" id="sp-method"></p></section>' +
  '</div></aside>';
trendRow.parentNode.appendChild(row);

function setPage(p) {
  state.page = p;
  pages.querySelectorAll("button").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.page === p)); });
  var sp = p === "species";
  document.documentElement.dataset.portal = sp ? "species" : "trends";      // the species portal has its own background tint
  trendRow.hidden = sp; trendSide.hidden = sp; $("screens").hidden = sp; spModes.hidden = !sp;
  $("modes").hidden = sp || document.querySelector('#screens [data-screen="analysis"]').getAttribute("aria-pressed") === "true";
  row.hidden = !sp; side.hidden = !sp;
  if (history.replaceState) history.replaceState(null, "", sp ? "#species" : location.pathname + location.search);
  if (sp && !state.list.length) start(); else if (sp) refresh();
}

/* ---------------- API ---------------- */
function api(path) {
  return fetch(API + path).then(function (r) {
    if (r.ok) return r.json();
    return r.json().catch(function () { return {}; }).then(function (b) { throw new Error(b.detail || ("HTTP " + r.status)); });
  });
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
  state.species = name; state.data = null;
  status("Computing " + name + "… The first request for a species can take up to a minute (its sightings are downloaded from OBIS).");
  return api("/habitat/especies/" + encodeURIComponent(name)).then(function (d) {
    if (state.species !== name) return;
    prepare(d); state.data = d; state.species = d.nombre;
    if (!d.idoneidad_chl) state.layer = "temp";
    var years = layerYears();
    if (!state.year || years.indexOf(state.year) < 0) state.year = years[years.length - 1];
    status(""); panel(); refresh(); return loadList(d.nombre);
  }).catch(function (e) { status("Could not compute " + name + ": " + e.message); });
}

function prepare(d) {                          // lookups used by the map
  var r = d.region, k;
  state.cellOf = new Int32Array(r.nx * r.ny).fill(-1);
  d.ocean_index.forEach(function (flat, i) { state.cellOf[flat] = i; });
  var n = d.ocean_index.length, last = d.idoneidad.slice(-NOW_YEARS);
  state.now = new Float64Array(n);
  for (k = 0; k < n; k++) { var s = 0, c = 0; last.forEach(function (rowv) { if (rowv[k] !== null) { s += rowv[k]; c++; } }); state.now[k] = c ? s / c / 100 : NaN; }
  var slopes = d.tendencia_celdas.slope_per_decade.filter(has).map(Math.abs).sort(function (a, b) { return a - b; });
  state.slopeMax = Math.max(0.005, slopes[Math.floor(slopes.length * 0.98)] || 0.005);
  d.seen = {};                                  // cell -> {year: n}
  d.avistamientos.celda.forEach(function (flat, i) { (d.seen[flat] = d.seen[flat] || {})[d.avistamientos.anio[i]] = d.avistamientos.n[i]; });
}

function status(t) { $("sp-status").textContent = t; $("sp-status").hidden = !t; }
function layerYears() { var d = state.data; return state.layer === "chl" && d.idoneidad_chl ? d.idoneidad_chl.anios : d.anios; }
function layerValues(year) { var d = state.data, ys = layerYears(), rows = state.layer === "chl" && d.idoneidad_chl ? d.idoneidad_chl.valores : d.idoneidad; return rows[ys.indexOf(year)]; }
function trendClass(k) { var t = state.data.tendencia_celdas, b = t.slope_per_decade[k]; if (!has(b)) return -1; return has(t.p_adj[k]) && t.p_adj[k] < ALPHA ? (b > 0 ? 2 : 0) : 1; }
function nowClass(k) { var v = state.now[k]; return isNaN(v) ? -1 : v < state.data.umbral_idoneo ? 0 : v < 0.5 ? 1 : 2; }

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
  var a = state.data.indicadores.area, years = a.anios, max = Math.max.apply(null, a.serie.filter(has).concat([1]));
  var box = $("sp-bars"); box.innerHTML = ""; box.style.gridTemplateColumns = "repeat(" + years.length + ",1fr)";
  years.forEach(function (y, i) {
    var v = a.serie[i] || 0, b = el("div"); b.style.height = Math.max(2, 100 * v / max) + "%";
    b.title = y + ": " + Math.round(v).toLocaleString("en-GB") + " km² suitable (" + a.pct_del_mar[i] + " % of the sea)"; if (y === state.year && state.mode === "suit") b.className = "on";
    b.addEventListener("click", function () { state.mode = "suit"; state.layer = "temp"; setYear(y); });
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
  $("sp-world-note").textContent = "Each mark is a 2° cell with sightings used for the profile. The box is the Mediterranean map above. " +
    (d.registros_sin_anio ? d.registros_sin_anio.toLocaleString("en-GB") + " records without a year in range and " : "") + d.registros_en_tierra.toLocaleString("en-GB") + " records far from the sea were left out.";
}

function setYear(y) { state.year = y; refresh(); }

/* ---------------- legend, sidebar state ---------------- */
function refresh() {
  if (state.page !== "species") return;
  var d = state.data;
  spModes.querySelectorAll("button").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.mode === state.mode)); });
  $("sp-year-box").hidden = state.mode !== "suit";
  if (!d) { draw(); return; }
  var years = layerYears(), slider = $("sp-year");
  if (years.indexOf(state.year) < 0) state.year = years[years.length - 1];
  slider.min = years[0]; slider.max = years[years.length - 1]; slider.value = state.year;
  var seenYear = 0; d.avistamientos.anio.forEach(function (a) { if (a === state.year) seenYear++; });
  $("sp-year-name").textContent = state.year + (seenYear ? " · seen in " + seenYear + " cells" : "");
  document.querySelectorAll("#sp-layer button").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.layer === state.layer)); });
  $("sp-layer-note").textContent = state.layer === "chl" ? "Temperature and chlorophyll together, " + years[0] + " to " + years[years.length - 1] + ". Trend maps always use the temperature index."
    : "Coldest month, hottest month and unusually hot months, " + d.anios[0] + " to " + d.anios[d.anios.length - 1] + ".";
  var L = $("sp-legend"), land = '<span><span class="sw land"></span>land</span>';
  if (state.mode === "suit") L.innerHTML = '<span>Habitat suitability: 0 <span class="ramp sp"></span> 1</span><span><span class="sp-dot"></span>seen in ' + state.year + '</span><span><span class="sp-dot faint"></span>seen in other years</span>' + land;
  else if (state.mode === "trend") L.innerHTML = '<span>Suitability falling<span class="ramp"></span>rising</span><span>range ±' + state.slopeMax.toFixed(2) + ' per decade, ' + d.anios[0] + '–' + d.anios[d.anios.length - 1] +
    '</span><span><span class="sw hatch"></span>not significant (adjusted p ≥ ' + ALPHA + ')</span>' + land;
  else {
    var grid = '<span class="bi">'; [2, 1, 0].forEach(function (n) { [0, 1, 2].forEach(function (t) { grid += '<i style="background:' + (BI[n][t] || css("--mid")) + '" title="' + NOW_WORD[n] + ", " + TREND_WORD[t] + '"></i>'; }); });
    L.innerHTML = grid + '</span><span>rows, bottom to top: suitability of the last ' + NOW_YEARS + ' years below ' + d.umbral_idoneo + ' · up to 0.5 · above<br>columns, left to right: suitability falling · no significant trend · rising, since ' + d.anios[0] + '</span>' + land;
  }
  bars(); draw();
}

/* ---------------- map ---------------- */
var canvas = $("sp-map"), geom = null, tip = $("tip");
function cellColor(k, values) {
  var midc = rgb(css("--mid"));
  if (state.mode === "suit") { var v = values[k]; return v === null ? null : mix(midc, rgb(css("--neg")), Math.pow(v / 100, 0.9)); }
  if (state.mode === "trend") { var b = state.data.tendencia_celdas.slope_per_decade[k]; if (!has(b)) return null; var t = Math.max(-1, Math.min(1, b / state.slopeMax)); return mix(midc, rgb(css(t < 0 ? "--neg" : "--pos")), Math.pow(Math.abs(t), 0.8)); }
  var n = nowClass(k), c = trendClass(k); if (n < 0 || c < 0) return null; return rgb(BI[n][c] || css("--mid"));
}
function hatchPattern(g) {
  var cv = document.createElement("canvas"); cv.width = cv.height = 6; var h = cv.getContext("2d");
  h.strokeStyle = css("--ink"); h.globalAlpha = 0.5; h.lineWidth = 1; h.beginPath(); h.moveTo(-1, 7); h.lineTo(7, -1); h.stroke();
  return g.createPattern(cv, "repeat");
}
function draw() {
  if (state.page !== "species") return;
  var f = fit(canvas), g = f.g, w = f.w, h = f.h; if (!w || !h) return;
  var d = state.data; if (!d) { geom = null; return; }
  var r = d.region, nx = r.nx, ny = r.ny, s = Math.min((w - 90) / nx, (h - 80) / ny), ox = (w - nx * s) / 2 + 10, oy = (h - ny * s) / 2;
  geom = { s: s, ox: ox, oy: oy };
  var img = document.createElement("canvas"); img.width = nx; img.height = ny;
  var ig = img.getContext("2d"), im = ig.createImageData(nx, ny), land = rgb(css("--land")), nodata = rgb(css("--rule")), values = state.mode === "suit" ? layerValues(state.year) : null;
  for (var fl = 0; fl < nx * ny; fl++) { var k = state.cellOf[fl], c = k < 0 ? land : (cellColor(k, values) || nodata), o = fl * 4; im.data[o] = c[0]; im.data[o + 1] = c[1]; im.data[o + 2] = c[2]; im.data[o + 3] = k < 0 ? 150 : 255; }
  ig.putImageData(im, 0, 0); g.imageSmoothingEnabled = false; g.drawImage(img, ox, oy, nx * s, ny * s);
  if (state.mode === "trend") {
    var t = d.tendencia_celdas; g.fillStyle = hatchPattern(g);
    d.ocean_index.forEach(function (flat, k2) { if (has(t.slope_per_decade[k2]) && !(t.p_adj[k2] < ALPHA)) g.fillRect(ox + (flat % nx) * s, oy + Math.floor(flat / nx) * s, s + 0.5, s + 0.5); });
  }
  if (state.mode === "suit") {                  // sightings: other years small and faint, the chosen year on top
    var probe = css("--probe"), X = function (flat) { return ox + (flat % nx + 0.5) * s; }, Y = function (flat) { return oy + (Math.floor(flat / nx) + 0.5) * s; };
    g.fillStyle = probe; g.globalAlpha = 0.3;
    Object.keys(d.seen).forEach(function (flat) { if (!d.seen[flat][state.year]) { g.beginPath(); g.arc(X(+flat), Y(+flat), 1.5, 0, 7); g.fill(); } });
    g.globalAlpha = 1; g.strokeStyle = css("--sheet"); g.lineWidth = 1;
    Object.keys(d.seen).forEach(function (flat) { var n = d.seen[flat][state.year]; if (n) { g.beginPath(); g.arc(X(+flat), Y(+flat), Math.min(6, 3 + Math.log10(n) * 1.5), 0, 7); g.fill(); g.stroke(); } });
  }
  g.strokeStyle = css("--ink-2"); g.lineWidth = 0.8; g.strokeRect(ox, oy, nx * s, ny * s);
  for (var lon = Math.ceil(r.lon_west / 5) * 5; lon <= r.lon_west + nx * r.cell_deg; lon += 5) label(g, Math.abs(lon) + "°" + (lon >= 0 ? "E" : "W"), ox + (lon - r.lon_west) / r.cell_deg * s, oy + ny * s + 12, "center");
  for (var lat = Math.floor(r.lat_north / 5) * 5; lat >= r.lat_north - ny * r.cell_deg; lat -= 5) label(g, lat + "°N", ox - 6, oy + (r.lat_north - lat) / r.cell_deg * s, "right");
  var title = state.mode === "suit" ? state.year + " · habitat suitability" + (state.layer === "chl" ? " (temperature and chlorophyll)" : "") : state.mode === "trend" ? "Trend of habitat suitability per decade" : "Suitability now × its trend";
  g.font = "500 13px 'IBM Plex Mono',monospace"; g.fillStyle = css("--ink"); g.textAlign = "left"; g.textBaseline = "middle"; g.fillText(title, ox, oy - 14);
  if (state.mode !== "bi") {                      // line of the poleward limit of the year (or of the first and last year)
    var lim = d.indicadores.limite, yy = state.mode === "suit" ? [state.year] : [], dash = css("--probe");
    yy.forEach(function (y) { var v = lim.serie[lim.anios.indexOf(y)]; if (!has(v) || state.layer === "chl") return; var py = oy + (r.lat_north - v) / r.cell_deg * s;
      g.beginPath(); g.moveTo(ox, py); g.lineTo(ox + nx * s, py); g.strokeStyle = dash; g.lineWidth = 1.2; g.setLineDash([5, 4]); g.stroke(); g.setLineDash([]);
      g.font = "400 10px 'IBM Plex Mono',monospace"; g.fillStyle = css("--ink"); g.textAlign = "right"; g.fillText("poleward limit " + v.toFixed(1) + "°N", ox + nx * s - 4, py - 7); });
  }
}

canvas.addEventListener("mousemove", function (ev) {
  var d = state.data; if (!d || !geom) return;
  var rc = canvas.getBoundingClientRect(), r = d.region, i = Math.floor((ev.clientX - rc.left - geom.ox) / geom.s), j = Math.floor((ev.clientY - rc.top - geom.oy) / geom.s);
  if (i < 0 || i >= r.nx || j < 0 || j >= r.ny) { tip.style.opacity = 0; return; }
  var flat = j * r.nx + i, k = state.cellOf[flat], lat = r.lat_north - (j + 0.5) * r.cell_deg, lon = r.lon_west + (i + 0.5) * r.cell_deg;
  var where = deg(lat, "N", "S") + ", " + deg(lon, "E", "W"), t;
  if (k < 0) t = where + " · land";
  else {
    var v = layerValues(state.year)[k], tc = d.tendencia_celdas, li = state.layer === "temp" ? d.limitante[d.anios.indexOf(state.year)][k] : null, seen = d.seen[flat];
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
var timer;
$("sp-find").addEventListener("input", function (ev) {
  clearTimeout(timer); var q = ev.target.value.trim(); if (q.length < 3) return;
  timer = setTimeout(function () {
    api("/especies/buscar?q=" + encodeURIComponent(q)).then(function (res) {
      $("sp-suggest").innerHTML = res.map(function (r) { return '<option value="' + r.nombre + '">' + (r.familia || r.clase || "") + "</option>"; }).join("");
    }).catch(function () { /* suggestions are optional */ });
  }, 300);
});
$("sp-find").addEventListener("change", function (ev) { var q = ev.target.value.trim(); if (q.split(/\s+/).length >= 2) show(q.charAt(0).toUpperCase() + q.slice(1).toLowerCase()); });
$("sp-year").addEventListener("input", function (ev) { setYear(Number(ev.target.value)); });
document.querySelectorAll("#sp-layer button").forEach(function (b) { b.addEventListener("click", function () { state.layer = b.dataset.layer; state.mode = "suit"; refresh(); }); });

function start() {
  var wanted = new URLSearchParams(location.search).get("especie");
  loadList().then(function (list) {
    var first = wanted ? { nombre: wanted } : list.filter(function (sp) { return sp.nombre === "Octopus vulgaris"; })[0] || list[0];
    if (first) show(first.nombre);
    else status("No species stored yet. Type a scientific name on the left.");
  }).catch(function (e) { status("The species service is not available right now (" + e.message + ")."); });
}

/* ---------------- Variables portal: choose the data set (region + period) ---------------- */
// The Trends screen (app.js) reads one set at a time; changing it reloads the page with ?datos=<set>.
(function dataSetPicker() {
  var place = $("region-name"); if (!place) return;
  var current = new URLSearchParams(location.search).get("datos") || "mediterraneo-1980";
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

function redraw() { if (state.page === "species" && state.data) { draw(); panelCharts(); } }
var chartRaf = 0;
function panelCharts() { if (!chartRaf) chartRaf = requestAnimationFrame(function () { chartRaf = 0; if (state.data) panel(); }); }
if (window.ResizeObserver) { new ResizeObserver(draw).observe(row.querySelector(".stage")); new ResizeObserver(function () { if (state.page === "species" && state.data) panelCharts(); }).observe(row.querySelector(".panel")); }
if (window.matchMedia) window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", redraw);
if (document.fonts && document.fonts.ready) document.fonts.ready.then(redraw);
window.TrendSpecies = { state: state, show: show, refresh: refresh };   // for debugging in the console
if (location.hash === "#species") setPage("species");
})();
