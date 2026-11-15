/* Species page for Trend Detective. Self-contained: it adds a "Trends | Species" switch and its own
   sidebar, map and panel, and leaves app.js untouched. All computing happens in the Python backend
   (api.py + especies.py); this file only calls it with fetch() and draws the result.
   Results are yearly (monthly sea data averaged per year, 1980 to the last complete year).
   A species that is not stored yet is computed by the backend on its first request (a few seconds). */
(function () {
"use strict";

var API = "/api";      // same origin: nginx forwards /api/ to the backend
var VARIABLE_NAMES = {
  temperatura: "Surface temperature", salinidad: "Salinity", fitoplancton: "Chlorophyll (phytoplankton)",
  fluorescencia: "Chlorophyll fluorescence", dif_temp_mar_aire: "Sea–air temperature difference",
  evaporacion: "Evaporation", viento_velocidad: "Wind speed"
};
var COMMON_NAMES = {   // the backend stores Spanish common names; the site is in English
  "Octopus vulgaris": "Common octopus", "Sepia officinalis": "Common cuttlefish",
  "Loligo vulgaris": "European squid", "Eledone moschata": "Musky octopus",
  "Eledone cirrhosa": "Horned octopus", "Illex coindetii": "Broadtail shortfin squid",
  "Todarodes sagittatus": "European flying squid"
};
// Map windows (cell edges). The backend computes the whole ocean; the view only chooses what is drawn.
// k shrinks longitude (cos of the middle latitude) so shapes are not stretched. The polar rows of the
// world are cut, they have almost no data. The cell size comes from the backend (paso_grados, 1° by default).
var VIEWS = {
  med:   { name: "Mediterranean Sea", lon0: -6, lon1: 37, lat0: 30, lat1: 46, k: Math.cos(38 * Math.PI / 180), lonStep: 5, latStep: 5 },
  world: { name: "World ocean", lon0: -180, lon1: 180, lat0: -78, lat1: 84, k: 1, lonStep: 60, latStep: 30 }
};
var STEP = 1;

var $ = function (id) { return document.getElementById(id); };
function el(tag, cls, txt) { var e = document.createElement(tag); if (cls) e.className = cls; if (txt !== undefined) e.textContent = txt; return e; }
function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
function commonName(sp) { return COMMON_NAMES[sp.nombre] || sp.nombre_comun || ""; }
function varName(v) { return VARIABLE_NAMES[v] || v; }

// mode: "time" (stack of years) | "species" (stack of species) | "trend"; focus: one stack layer opened flat
var state = { page: "trends", species: null, year: null, data: null, list: [], view: "med", screen: "viz", mode: "time", focus: false };

/* ---------------- DOM: page switch, sidebar, main view, panel ---------------- */
var options = document.querySelector(".options");
var pages = el("span", "seg"); pages.id = "pages"; pages.setAttribute("role", "group"); pages.setAttribute("aria-label", "Page");
[["trends", "Trends"], ["species", "Species"]].forEach(function (p) {
  var b = el("button", "", p[1]); b.type = "button"; b.dataset.page = p[0]; b.setAttribute("aria-pressed", String(p[0] === "trends"));
  b.addEventListener("click", function () { setPage(p[0]); }); pages.appendChild(b);
});
options.insertBefore(pages, options.firstChild);

// Same two button groups as the Trends page, with the Species choices. Each page shows its own pair.
function seg(id, label, items, on) {
  var g = el("span", "seg"); g.id = id; g.hidden = true; g.setAttribute("role", "group"); g.setAttribute("aria-label", label);
  items.forEach(function (it) {
    var b = el("button", "", it[1]); b.type = "button"; b.dataset.v = it[0];
    b.addEventListener("click", function () { on(it[0]); }); g.appendChild(b);
  });
  options.appendChild(g); return g;
}
function press(g, v) { g.querySelectorAll("button").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.v === v)); }); }
var spScreens = seg("sp-screens", "Screen", [["viz", "Visualisation"], ["analysis", "Analysis"]], setScreen);
var spModes = seg("sp-modes", "Visualisation options", [["time", "Stack by time"], ["species", "Stack by species"], ["trend", "Trend map"]], setMode);
press(spScreens, state.screen); press(spModes, state.mode);
function setScreen(v) { state.screen = v; press(spScreens, v); setPage(state.page); }   // Analysis is empty for now
function setMode(v) { state.mode = v; state.focus = false; press(spModes, v); refresh(); }

var trendSide = document.querySelector(".side .side-body");
var side = el("div", "side-body"); side.id = "sp-side"; side.hidden = true;
side.innerHTML =
  '<section><h2>Region</h2><p class="fact" id="sp-region">&nbsp;</p>' +
  '<select id="sp-view" class="sp-select" aria-label="Region shown on the map">' +
  Object.keys(VIEWS).map(function (v) { return '<option value="' + v + '">' + VIEWS[v].name + '</option>'; }).join("") +
  '</select><p class="fact dim" id="sp-period">&nbsp;</p></section>' +
  '<section><h2>Species</h2><p class="fact dim">Stored species. Cephalopods are always ready.</p><ul class="varlist" id="sp-list"></ul></section>' +
  '<section class="sp-search"><h2>Find another species</h2>' +
  '<input id="sp-find" list="sp-suggest" placeholder="Scientific name, e.g. Thunnus thynnus" autocomplete="off" aria-label="Scientific name">' +
  '<datalist id="sp-suggest"></datalist><p class="fact dim">Computed on first request, then stored.</p></section>' +
  '<section><h2>What is this?</h2><p class="fact dim">Each sighting (OBIS, since 1980) is matched to the sea conditions of its place and year ' +
  '(monthly data averaged per year). Habitat suitability (0 to 1) says how close each place and year is to the conditions where the species has been seen. ' +
  'It is a statistical match, not proof of cause.</p></section>';
trendSide.parentNode.appendChild(side);

var trendRow = $("row");
var row = el("div", "row"); row.id = "sp-row"; row.hidden = true;
row.innerHTML =
  '<main class="main"><div class="legend" id="sp-legend"></div>' +
  '<div class="stage"><canvas id="sp-map" aria-label="Habitat suitability map"></canvas><p class="loading" id="sp-status">Loading…</p></div>' +
  '<div class="stackbar"><button type="button" class="ghost" id="sp-back" hidden>&larr; Back to stack</button>' +
  '<button type="button" class="round" id="sp-prev" aria-label="Previous year">&#9664;</button>' +
  '<select id="sp-year" class="sp-select" aria-label="Year"></select>' +
  '<button type="button" class="round" id="sp-next" aria-label="Next year">&#9654;</button>' +
  '<span class="fact dim" id="sp-hint"></span></div></main>' +
  '<aside class="panel" aria-label="Species details"><div class="sp-panel">' +
  '<div><div class="panel-head"><h2 id="sp-title">&nbsp;</h2></div><p class="fact dim" id="sp-sub">&nbsp;</p></div>' +
  '<section><h2>Favourable sea area by year</h2><div class="sp-bars" id="sp-bars"></div><div class="sp-years" id="sp-years"></div>' +
  '<p class="fact dim" id="sp-trend">&nbsp;</p></section>' +
  '<section><h2>What limits its habitat</h2><ul class="sp-limits" id="sp-limits"></ul></section>' +
  '<section><h2>Conditions where it is seen</h2><table class="allvars"><thead><tr><th>Variable</th><th>Typical range</th><th>Median</th></tr></thead><tbody id="sp-niche"></tbody></table>' +
  '<p class="note">Typical range: 5th to 95th percentile of the conditions at its sightings. ' +
  'Variables marked * cover every year and make the yearly suitability; the others start later (satellites) and only describe the niche.</p></section>' +
  '<section><h2>Place</h2><p class="fact dim sp-cell" id="sp-cell">&nbsp;</p></section>' +
  '</div></aside>';
trendRow.parentNode.appendChild(row);


function setPage(p) {
  state.page = p;
  document.documentElement.dataset.page = p;   // species.css colours the page by it
  pages.querySelectorAll("button").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.page === p)); });
  var sp = p === "species";
  trendRow.hidden = sp; trendSide.hidden = sp; $("screens").hidden = sp;
  $("modes").hidden = sp || document.querySelector('#screens [data-screen="analysis"]').getAttribute("aria-pressed") === "true";
  spScreens.hidden = !sp; spModes.hidden = !sp || state.screen === "analysis";
  row.hidden = !sp || state.screen === "analysis"; side.hidden = !sp;
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
  return api("/especies").then(function (list) {
    state.list = list;
    var ul = $("sp-list"); ul.innerHTML = "";
    list.forEach(function (sp) {
      var li = el("li"), b = el("button"); b.type = "button";
      b.setAttribute("aria-pressed", String(sp.nombre === (select || state.species)));
      var name = el("span", "name"); name.appendChild(el("span", "sp-name", sp.nombre)); name.appendChild(el("span", "sp-common", commonName(sp)));
      b.appendChild(name); b.addEventListener("click", function () { show(sp.nombre); });
      li.appendChild(b); ul.appendChild(li);
    });
    return list;
  });
}

function show(name) {
  state.species = name;
  status("Computing " + name + "… The first request for a species can take up to a minute (its sightings are downloaded from OBIS).");
  return api("/especies/" + encodeURIComponent(name)).then(function (d) {
    if (state.species !== name) return;
    state.data = d; state.species = d.nombre;
    var years = d.anios, seen = d.resumen.avistamientos_por_anio;
    $("sp-year").innerHTML = years.slice().reverse().map(function (y) {
      return '<option value="' + y + '">' + y + " · " + (seen[y] || 0) + " sightings</option>";
    }).join("");
    if (!state.year || years.indexOf(state.year) < 0) state.year = years[years.length - 1];
    setYear(state.year, true);
    panel(); return loadList(d.nombre).then(refresh);
  }).catch(function (e) { status("Could not compute " + name + ": " + e.message); });
}

function status(t) { $("sp-status").textContent = t; $("sp-status").hidden = !t; }

/* ---------------- panel ---------------- */
function fmt(x) { var a = Math.abs(x); return a >= 1e5 || (a > 0 && a < 1e-3) ? x.toExponential(2) : a >= 100 ? x.toFixed(0) : a >= 1 ? x.toFixed(2) : x.toPrecision(3); }

function panel() {
  var d = state.data, r = d.resumen;
  $("sp-title").innerHTML = "<i>" + d.nombre + "</i>";
  $("sp-sub").textContent = [commonName(d), d.registros_usados + " of " + d.registros + " sightings used",
    "updated " + new Date(d.calculado).toLocaleDateString()].filter(Boolean).join(" · ");
  $("sp-period").textContent = "Yearly results " + r.periodo[0] + " to " + r.periodo[1];
  var t = r.tendencia_pp_por_decada, top = Math.max.apply(null, d.anios.map(function (y) { return r.superficie_favorable_pct[y] || 0; }));
  $("sp-trend").textContent = "Share of the world ocean; the tallest bar is " + top.toFixed(1) + "%. " + (t === null ? "" : "Trend: " + (t > 0 ? "+" : t < 0 ? "−" : "") + Math.abs(t).toFixed(1) +
    " percentage points per decade (" + r.periodo[0] + "–" + r.periodo[1] + ", least-squares line).");

  var lim = Object.keys(r.factores_limitantes_pct);
  $("sp-limits").innerHTML = lim.length ? lim.map(function (v) {
    var p = r.factores_limitantes_pct[v];
    return '<li><span>' + varName(v) + '</span><span class="pct">' + Math.round(p) + '%</span><span class="bar" style="width:' + p + '%"></span></li>';
  }).join("") : '<li><span class="dim">Favourable everywhere with the data available.</span></li>';

  $("sp-niche").innerHTML = Object.keys(d.nicho).map(function (v) {
    var n = d.nicho[v], mark = r.variables_indice.indexOf(v) >= 0 ? " *" : "";
    var units = n.unidades.replace("día", "day");   // backend units are written in Spanish
    return "<tr><td>" + varName(v) + mark + '</td><td class="num">' + fmt(n.p5) + " – " + fmt(n.p95) + " " + units +
           '</td><td class="num">' + fmt(n.p50) + "</td></tr>";
  }).join("");

  var years = d.anios, mid = years[Math.floor(years.length / 2)];
  $("sp-years").innerHTML = "<span>" + years[0] + "</span><span>" + mid + "</span><span>" + years[years.length - 1] + "</span>";
  bars();
}

function bars() {
  if (!state.data) return;
  var pct = state.data.resumen.superficie_favorable_pct, years = state.data.anios;
  var box = $("sp-bars"); box.innerHTML = ""; box.style.gridTemplateColumns = "repeat(" + years.length + ",1fr)";
  var top = Math.max.apply(null, years.map(function (y) { return pct[y] || 0; })) || 1;   // bars are relative to the best year
  years.forEach(function (y) {
    var v = pct[y] || 0, b = el("div"); b.style.height = Math.max(2, 100 * v / top) + "%";
    b.title = y + ": " + v + "% of the sea is favourable"; if (y === state.year) b.className = "on";
    b.addEventListener("click", function () { setYear(y); });
    box.appendChild(b);
  });
}

function setYear(y, quiet) {
  state.year = y; $("sp-year").value = y;
  var years = state.data ? state.data.anios : [y];
  $("sp-prev").disabled = y <= years[0]; $("sp-next").disabled = y >= years[years.length - 1];
  if (!quiet) { bars(); refresh(); }
}

/* ---------------- maps: a stack of layers, one opened layer, or the trend map ---------------- */
var canvas = $("sp-map"), geom = null, shown = null, hits = [];
var ISO = { ux: 1, uy: 0.10, vx: -0.35, vy: 0.30 };   // same slant as the Trends stack (app.js)
var loaded = {}, pending = {}, images = {}, trends = {};

function need(name, year) {              // the backend map if it is here; else ask for it and redraw when it arrives
  var key = name + "|" + year, m = loaded[key];
  if (m) { delete loaded[key]; loaded[key] = m; return m; }      // re-insert: the oldest key is the least used
  if (!pending[key]) pending[key] = api("/especies/" + encodeURIComponent(name) + "/mapa?anio=" + year).then(function (m) {
    STEP = m.paso_grados || STEP; delete pending[key];
    var keys = Object.keys(loaded); if (keys.length >= 16) delete loaded[keys[0]];   // a world map is about 1 MB
    m.seen = m.avistamientos.reduce(function (n, a) { return n + a[3]; }, 0);
    loaded[key] = m; draw();
  }).catch(function (e) { delete pending[key]; status("Map not available: " + e.message); });
  return null;
}

function trendFor(name) {                // change of suitability per decade in every cell, computed once per species
  var T = trends[name]; if (T) return T.puntos ? T : null;
  trends[name] = {};
  // ponytail: least-squares slope from at most 12 evenly spaced years, not every year (each year is a 1 MB
  // download); use all years when the backend offers the trend map as one endpoint.
  var all = state.data.anios, step = Math.ceil(all.length / 12), acc = {}, done = 0;
  var ys = all.filter(function (y, i) { return (all.length - 1 - i) % step === 0; });
  function next() {
    if (done === ys.length) return;
    if (state.species === name && state.mode === "trend") status("Computing the trend: map " + (done + 1) + " of " + ys.length + "…");
    return api("/especies/" + encodeURIComponent(name) + "/mapa?anio=" + ys[done]).then(function (m) {
      STEP = m.paso_grados || STEP; var x = ys[done] - ys[0];
      m.puntos.forEach(function (p) {
        var k = p[0] + "," + p[1], a = acc[k] || (acc[k] = [p[0], p[1], 0, 0, 0, 0, 0]);
        a[2]++; a[3] += x; a[4] += p[2]; a[5] += x * x; a[6] += x * p[2];
      });
      done++; return next();
    });
  }
  next().then(function () {
    var n = ys.length, pts = [], abs = [];
    Object.keys(acc).forEach(function (k) {
      var a = acc[k]; if (a[2] < n) return;                       // only cells with a value in every sampled year
      var sl = 10 * (n * a[6] - a[3] * a[4]) / (n * a[5] - a[3] * a[3]); pts.push([a[0], a[1], sl]); abs.push(Math.abs(sl));
    });
    abs.sort(function (p, q) { return p - q; });
    trends[name] = { puntos: pts, scale: Math.max(0.01, abs[Math.floor(abs.length * 0.98)] || 0), years: ys, step: step };
    refresh();
  }).catch(function (e) { delete trends[name]; status("Trend not available: " + e.message); });
  return null;
}

function layers() {                      // what the stack shows, top layer first (four at most)
  var d = state.data; if (!d) return [];
  if (state.mode === "time") {           // the chosen year on top, the three years before it below
    var ys = d.anios, i = Math.max(ys.indexOf(state.year), Math.min(3, ys.length - 1));
    return ys.slice(Math.max(0, i - 3), i + 1).reverse().map(function (y) { return { name: d.nombre, year: y, label: String(y), sel: y === state.year }; });
  }
  var L = state.list, j = 0; L.forEach(function (sp, k) { if (sp.nombre === d.nombre) j = k; });
  var a = Math.max(0, Math.min(j - 1, L.length - 4));
  return L.slice(a, a + 4).map(function (sp) { return { name: sp.nombre, year: state.year, label: sp.nombre, sel: sp.nombre === d.nombre, italic: true }; });
}

function rgb(h) { h = h.replace("#", ""); if (h.length === 3) h = h.replace(/./g, "$&$&"); return [0, 2, 4].map(function (i) { return parseInt(h.substr(i, 2), 16); }); }

function paint(g, V, s, data, trend) {   // one map in a rectangle at the origin: land, sea cells, sightings
  var fw = (V.lon1 - V.lon0) * V.k * s, fh = (V.lat1 - V.lat0) * s, cw = STEP * V.k * s, ch = STEP * s;
  function X(lon) { return (lon - V.lon0) * V.k * s; }
  function Y(lat) { return (V.lat1 - lat) * s; }
  g.fillStyle = css("--land"); g.fillRect(0, 0, fw, fh);
  if (!data) return;
  g.save(); g.beginPath(); g.rect(0, 0, fw, fh); g.clip();
  var lo = rgb(css("--pos")), mid = rgb(css("--mid")), hi = rgb(css("--neg"));
  data.puntos.forEach(function (p) {
    if (p[0] < V.lat0 - STEP || p[0] > V.lat1 + STEP || p[1] < V.lon0 - STEP || p[1] > V.lon1 + STEP) return;
    var t = Math.max(-1, Math.min(1, trend ? p[2] / data.scale : Math.max(0, p[2]))), to = t < 0 ? lo : hi; t = Math.abs(t);
    g.fillStyle = "rgb(" + mid.map(function (c, i) { return Math.round(c + (to[i] - c) * t); }).join(",") + ")";
    g.fillRect(X(p[1]) - cw / 2, Y(p[0]) - ch / 2, cw + 0.6, ch + 0.6);
  });
  if (!trend) {                          // sightings: other years small and faint, the layer's year on top
    g.fillStyle = css("--probe"); g.globalAlpha = 0.35;
    data.avistamientos.forEach(function (a) { if (!a[3]) { g.beginPath(); g.arc(X(a[1]), Y(a[0]), 1.6, 0, 2 * Math.PI); g.fill(); } });
    g.globalAlpha = 1;
    data.avistamientos.forEach(function (a) {
      if (a[3]) { g.beginPath(); g.arc(X(a[1]), Y(a[0]), Math.min(6, 2.5 + Math.log10(a[3] + 1) * 2), 0, 2 * Math.PI); g.fill(); }
    });
  }
  g.restore();
}

function layerImage(L, m, V, s, dpr) {   // a stack layer painted once, then reused while nothing changes
  var key = [L.name, L.year, state.view, Math.round(s * 100), dpr, css("--mid"), m ? 1 : 0].join("|");
  if (images[key]) return images[key];
  if (Object.keys(images).length > 24) images = {};
  var cv = document.createElement("canvas");
  cv.width = Math.max(1, Math.ceil((V.lon1 - V.lon0) * V.k * s * dpr)); cv.height = Math.max(1, Math.ceil((V.lat1 - V.lat0) * s * dpr));
  var g = cv.getContext("2d"); g.scale(dpr, dpr); paint(g, V, s, m, false);
  return (images[key] = cv);
}

function label(g, t, x, y, o) {
  g.fillStyle = css(o.color || "--ink-2"); g.textAlign = o.align || "left"; g.textBaseline = "middle";
  g.font = (o.italic ? "italic " : "") + (o.weight || "400") + " " + o.size + "px " + css(o.mono ? "--mono" : "--body"); g.fillText(t, x, y);
}

function draw() {
  if (state.page !== "species" || state.screen !== "viz") return;
  var dpr = window.devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return;
  canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
  var g = canvas.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0);
  var V = VIEWS[state.view], spanX = (V.lon1 - V.lon0) * V.k, spanY = V.lat1 - V.lat0, d = state.data, trend = state.mode === "trend";
  var s, ox, oy, fw, fh;
  geom = null; shown = null; hits = [];
  if (!d) return;
  canvas.style.cursor = trend || state.focus ? "crosshair" : "pointer";

  if (trend || state.focus) {            // one flat map with degree labels
    var data = trend ? trendFor(d.nombre) : need(d.nombre, state.year);
    s = Math.min((w - 80) / spanX, (h - 80) / spanY); ox = (w - spanX * s) / 2; oy = (h - spanY * s) / 2; fw = spanX * s; fh = spanY * s;
    geom = { s: s, k: V.k, ox: ox, oy: oy, lon0: V.lon0, lat1: V.lat1 }; shown = data && { data: data, trend: trend };
    g.save(); g.translate(ox, oy); paint(g, V, s, data, trend); g.restore();
    g.strokeStyle = css("--ink-2"); g.lineWidth = 0.8; g.strokeRect(ox, oy, fw, fh);
    var lon, lat, ns = function (v, a, b) { return Math.abs(v) + "°" + (v > 0 ? a : v < 0 ? b : ""); };
    for (lon = Math.ceil(V.lon0 / V.lonStep) * V.lonStep; lon <= V.lon1; lon += V.lonStep)
      label(g, ns(lon, "E", "W"), ox + (lon - V.lon0) * V.k * s, oy + fh + 12, { align: "center", size: 10, mono: true });
    for (lat = Math.floor(V.lat1 / V.latStep) * V.latStep; lat >= V.lat0; lat -= V.latStep)
      label(g, ns(lat, "N", "S"), ox - 6, oy + (V.lat1 - lat) * s, { align: "right", size: 10, mono: true });
    if (data) status(""); else if (!trend) status("Loading the map…");
    return;
  }

  // Stack: every layer is a flat map slanted with the ISO vectors. Lower layers first, so the top one is in front.
  var Ls = layers(), gap = h / 4.6, unitW = spanX * ISO.ux - spanY * ISO.vx, missing = 0, i;
  s = Math.max(0.5, Math.min((w - 28) / unitW, (gap - 46) / (spanY * ISO.vy)));
  ox = (w - unitW * s) / 2 - spanY * ISO.vx * s; fw = spanX * s; fh = spanY * s;
  function at(a, b, top) { return [ox + a * ISO.ux + b * ISO.vx, top + a * ISO.uy + b * ISO.vy]; }
  for (i = Ls.length - 1; i >= 0; i--) {
    var L = Ls[i], m = need(L.name, L.year), top = 0.5 * gap - 4 + i * gap; if (!m) missing++;
    g.save(); g.transform(ISO.ux, ISO.uy, ISO.vx, ISO.vy, ox, top); g.drawImage(layerImage(L, m, V, s, dpr), 0, 0, fw, fh); g.restore();
    var c = [at(0, 0, top), at(fw, 0, top), at(fw, fh, top), at(0, fh, top)];
    g.beginPath(); c.forEach(function (p, k) { if (k) g.lineTo(p[0], p[1]); else g.moveTo(p[0], p[1]); }); g.closePath();
    g.strokeStyle = css(L.sel ? "--ink" : "--ink-2"); g.lineWidth = L.sel ? 2 : 0.8; g.stroke();
    label(g, L.label, c[3][0], c[3][1] + 30, { color: "--ink", weight: L.sel ? "500" : "400", size: 13, italic: L.italic });
    if (m) label(g, m.seen + " sightings in " + L.year, c[1][0], c[1][1] - 30, { align: "right", size: 11.5, mono: true });
    hits[i] = { L: L, top: top, fw: fw, fh: fh, ox: ox };
  }
  status(missing ? "Loading maps…" : "");
}

function refresh() {                     // legend and bottom bar follow the mode, then the map is drawn
  var trend = state.mode === "trend", T = trends[state.species], land = '<span><span class="sw land"></span>land</span>';
  $("sp-legend").innerHTML = trend
    ? '<span>Habitat suitability: losing <span class="ramp div"></span> gaining</span>' +
      (T && T.puntos ? "<span>range ±" + T.scale.toFixed(2) + " per decade · " + T.years[0] + "–" + T.years[T.years.length - 1] +
        (T.step > 1 ? ", one year in every " + T.step : "") + "</span>" : "") + land
    : '<span>Habitat suitability: 0 <span class="ramp"></span> 1</span>' +
      '<span><span class="sp-dot"></span>sightings that year</span><span><span class="sp-dot faint"></span>other years</span>' + land;
  $("sp-back").hidden = trend || !state.focus;
  ["sp-prev", "sp-year", "sp-next"].forEach(function (id) { $(id).hidden = trend; });
  $("sp-hint").textContent = trend || state.focus ? "Point at the map to read a place." : "Click a map to open it.";
  draw();
}

canvas.addEventListener("click", function (ev) {     // stack: open the layer under the pointer
  var r = canvas.getBoundingClientRect(), px = ev.clientX - r.left, py = ev.clientY - r.top, det = ISO.ux * ISO.vy - ISO.vx * ISO.uy, i, H, a, b;
  for (i = 0; i < hits.length; i++) {                // top layer first: it is the one in front
    H = hits[i]; a = ((px - H.ox) * ISO.vy - ISO.vx * (py - H.top)) / det; b = (ISO.ux * (py - H.top) - ISO.uy * (px - H.ox)) / det;
    if (a < 0 || a > H.fw || b < 0 || b > H.fh) continue;
    state.focus = true;
    if (H.L.name !== state.species) show(H.L.name); else setYear(H.L.year);
    return;
  }
});

canvas.addEventListener("mousemove", function (ev) {
  if (!shown || !geom) return;
  var r = canvas.getBoundingClientRect(), D = shown.data;
  var lon = geom.lon0 + (ev.clientX - r.left - geom.ox) / (geom.k * geom.s);
  var lat = geom.lat1 - (ev.clientY - r.top - geom.oy) / geom.s;
  if (!D.celda) {                        // "lat,lon" -> point, built on the first read-out
    D.celda = {}; D.puntos.forEach(function (p) { D.celda[p[0] + "," + p[1]] = p; });
    D.vistos = {}; (D.avistamientos || []).forEach(function (a) { D.vistos[a[0] + "," + a[1]] = a; });
  }
  // Centre of the cell under the pointer, written like the backend writes it.
  var cLat = Math.floor(lat / STEP) * STEP + STEP / 2, cLon = Math.floor(lon / STEP) * STEP + STEP / 2;
  var key = cLat + "," + cLon, p = D.celda[key], seen = D.vistos[key];
  if (!p) { $("sp-cell").textContent = "No sea data here."; return; }
  var ns = function (v, a, b) { return Math.abs(v).toFixed(1) + "°" + (v >= 0 ? a : b); }, t = ns(p[0], "N", "S") + ", " + ns(p[1], "E", "W");
  if (shown.trend) t += " · suitability " + (p[2] > 0 ? "+" : p[2] < 0 ? "−" : "") + Math.abs(p[2]).toFixed(3) + " per decade";
  else {
    t += " · suitability " + p[2].toFixed(2) + " in " + state.year;
    if (p[2] < D.umbral_idoneo && p[3] >= 0) t += " · limited by " + varName(D.limitantes[p[3]]).toLowerCase();
    if (seen) t += " · " + seen[3] + " sightings that year, " + seen[2] + " in all years";
  }
  $("sp-cell").textContent = t;
});

/* ---------------- search, year, start ---------------- */
var timer;
$("sp-find").addEventListener("input", function (ev) {
  clearTimeout(timer); var q = ev.target.value.trim(); if (q.length < 3) return;
  timer = setTimeout(function () {
    api("/especies/buscar?q=" + encodeURIComponent(q)).then(function (res) {
      $("sp-suggest").innerHTML = res.map(function (r) { return '<option value="' + r.nombre + '">' + (r.familia || r.clase || "") + "</option>"; }).join("");
    }).catch(function () { /* suggestions are optional */ });
  }, 300);
});
$("sp-find").addEventListener("change", function (ev) {
  var q = ev.target.value.trim(); if (q.split(/\s+/).length >= 2) show(q);
});
$("sp-year").addEventListener("change", function (ev) { setYear(Number(ev.target.value)); });
$("sp-prev").addEventListener("click", function () { setYear(state.year - 1); });
$("sp-next").addEventListener("click", function () { setYear(state.year + 1); });
$("sp-back").addEventListener("click", function () { state.focus = false; refresh(); });
function setView(v) { state.view = v; $("sp-view").value = v; $("sp-region").textContent = VIEWS[v].name; draw(); }
$("sp-view").addEventListener("change", function (ev) { setView(ev.target.value); });
setView(state.view);

function start() {
  loadList().then(function (list) {
    var first = list.filter(function (sp) { return sp.nombre === "Octopus vulgaris"; })[0] || list[0];
    if (first) show(first.nombre);
    else status("No species stored yet. Type a scientific name on the left.");
  }).catch(function (e) { status("The species service is not available right now (" + e.message + ")."); });
}

/* ---------------- Trends page: choose the data set (region + period) ---------------- */
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
             " variables, " + d.cell_deg + "° cells)</option>";
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

if (window.ResizeObserver) new ResizeObserver(draw).observe(row.querySelector(".stage"));
if (window.matchMedia) window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", draw);
if (location.hash === "#species") setPage("species");
window.addEventListener("hashchange", function () { setPage(location.hash === "#species" ? "species" : "trends"); });
})();
