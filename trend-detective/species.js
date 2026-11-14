/* Species page for Trend Detective. Self-contained: it adds a "Trends | Species" switch and its own
   sidebar, map and panel, and leaves app.js untouched. All computing happens in the Python backend
   (api.py + especies.py); this file only calls it with fetch() and draws the result.
   A species that is not stored yet is computed by the backend on its first request (~15 s). */
(function () {
"use strict";

var API = "/api";      // same origin: nginx forwards /api/ to the backend
var MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
              "September", "October", "November", "December"];
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
// Map window: same box as the backend grid (0.5° cells).
var LON0 = -6, LON1 = 36.5, LAT0 = 30, LAT1 = 46, STEP = 0.5;

var $ = function (id) { return document.getElementById(id); };
function el(tag, cls, txt) { var e = document.createElement(tag); if (cls) e.className = cls; if (txt !== undefined) e.textContent = txt; return e; }
function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
function commonName(sp) { return COMMON_NAMES[sp.nombre] || sp.nombre_comun || ""; }
function varName(v) { return VARIABLE_NAMES[v] || v; }

var state = { page: "trends", species: null, month: new Date().getMonth() + 1, data: null, map: null, list: [] };

/* ---------------- DOM: page switch, sidebar, main view, panel ---------------- */
var options = document.querySelector(".options");
var pages = el("span", "seg"); pages.id = "pages"; pages.setAttribute("role", "group"); pages.setAttribute("aria-label", "Page");
[["trends", "Trends"], ["species", "Species"]].forEach(function (p) {
  var b = el("button", "", p[1]); b.type = "button"; b.dataset.page = p[0]; b.setAttribute("aria-pressed", String(p[0] === "trends"));
  b.addEventListener("click", function () { setPage(p[0]); }); pages.appendChild(b);
});
options.insertBefore(pages, options.firstChild);

var trendSide = document.querySelector(".side .side-body");
var side = el("div", "side-body"); side.id = "sp-side"; side.hidden = true;
side.innerHTML =
  '<section><h2>Region</h2><p class="fact">Mediterranean Sea</p><p class="fact dim" id="sp-period">&nbsp;</p></section>' +
  '<section><h2>Species</h2><p class="fact dim">Stored species. Cephalopods are always ready.</p><ul class="varlist" id="sp-list"></ul></section>' +
  '<section class="sp-search"><h2>Find another species</h2>' +
  '<input id="sp-find" list="sp-suggest" placeholder="Scientific name, e.g. Thunnus thynnus" autocomplete="off" aria-label="Scientific name">' +
  '<datalist id="sp-suggest"></datalist><p class="fact dim">Computed on first request, then stored.</p></section>' +
  '<section class="sp-month"><h2>Month</h2><p class="fact" id="sp-month-name">&nbsp;</p>' +
  '<input type="range" id="sp-month" min="1" max="12" step="1" aria-label="Month"></section>' +
  '<section><h2>What is this?</h2><p class="fact dim">Each sighting (OBIS, since 2000) is matched to the sea conditions of its place and month. ' +
  'Habitat suitability (0 to 1) says how close each place and month is to the conditions where the species has been seen. ' +
  'It is a statistical match, not proof of cause.</p></section>';
trendSide.parentNode.appendChild(side);

var trendRow = $("row");
var row = el("div", "row"); row.id = "sp-row"; row.hidden = true;
row.innerHTML =
  '<main class="main"><div class="legend" id="sp-legend"></div>' +
  '<div class="stage"><canvas id="sp-map" aria-label="Habitat suitability map"></canvas><p class="loading" id="sp-status">Loading…</p></div></main>' +
  '<aside class="panel" aria-label="Species details"><div class="sp-panel">' +
  '<div><div class="panel-head"><h2 id="sp-title">&nbsp;</h2></div><p class="fact dim" id="sp-sub">&nbsp;</p></div>' +
  '<section><h2>Favourable sea area by month</h2><div class="sp-bars" id="sp-bars"></div><div class="sp-months" id="sp-months"></div></section>' +
  '<section><h2>What limits its habitat</h2><ul class="sp-limits" id="sp-limits"></ul></section>' +
  '<section><h2>Conditions where it is seen</h2><table class="allvars"><thead><tr><th>Variable</th><th>Typical range</th><th>Median</th></tr></thead><tbody id="sp-niche"></tbody></table>' +
  '<p class="note">Typical range: 5th to 95th percentile of the conditions at its sightings.</p></section>' +
  '<section><h2>Place</h2><p class="fact dim sp-cell" id="sp-cell">Point at the map to read a place.</p></section>' +
  '</div></aside>';
trendRow.parentNode.appendChild(row);

$("sp-months").innerHTML = MONTHS.map(function (m) { return "<span>" + m[0] + "</span>"; }).join("");
$("sp-legend").innerHTML = '<span>Habitat suitability: 0 <span class="ramp"></span> 1</span>' +
  '<span><span class="sp-dot"></span>sightings</span><span id="sp-legend-month"></span>';

function setPage(p) {
  state.page = p;
  pages.querySelectorAll("button").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.page === p)); });
  var sp = p === "species";
  trendRow.hidden = sp; trendSide.hidden = sp; $("screens").hidden = sp;
  $("modes").hidden = sp || document.querySelector('#screens [data-screen="analysis"]').getAttribute("aria-pressed") === "true";
  row.hidden = !sp; side.hidden = !sp;
  if (history.replaceState) history.replaceState(null, "", sp ? "#species" : location.pathname + location.search);
  if (sp && !state.list.length) start(); else if (sp) draw();
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
  status("Computing " + name + "… The first request for a species takes about 15 seconds.");
  return api("/especies/" + encodeURIComponent(name)).then(function (d) {
    if (state.species !== name) return;
    state.data = d; state.species = d.nombre;
    panel(); return loadList(d.nombre).then(loadMap);
  }).catch(function (e) { status("Could not compute " + name + ": " + e.message); });
}

function loadMap() {
  var name = state.species, month = state.month;
  return api("/especies/" + encodeURIComponent(name) + "/mapa?mes=" + month).then(function (m) {
    if (state.species !== name || state.month !== month) return;
    state.map = m; status(""); draw(); bars();
  }).catch(function (e) { status("Map not available: " + e.message); });
}

function status(t) { $("sp-status").textContent = t; $("sp-status").hidden = !t; }

/* ---------------- panel ---------------- */
function fmt(x) { var a = Math.abs(x); return a >= 1e5 || (a > 0 && a < 1e-3) ? x.toExponential(2) : a >= 100 ? x.toFixed(0) : a >= 1 ? x.toFixed(2) : x.toPrecision(3); }

function panel() {
  var d = state.data, r = d.resumen;
  $("sp-title").innerHTML = "<i>" + d.nombre + "</i>";
  $("sp-sub").textContent = [commonName(d), d.registros_usados + " of " + d.registros + " sightings used",
    "updated " + new Date(d.calculado).toLocaleDateString()].filter(Boolean).join(" · ");
  $("sp-period").textContent = r.periodo_climatologia ? "Sea conditions " + r.periodo_climatologia.replace("..", " to ") : "";

  var lim = Object.keys(r.factores_limitantes_pct);
  $("sp-limits").innerHTML = lim.length ? lim.map(function (v) {
    var p = r.factores_limitantes_pct[v];
    return '<li><span>' + varName(v) + '</span><span class="pct">' + Math.round(p) + '%</span><span class="bar" style="width:' + p + '%"></span></li>';
  }).join("") : '<li><span class="dim">Favourable everywhere with the data available.</span></li>';

  $("sp-niche").innerHTML = Object.keys(d.nicho).map(function (v) {
    var n = d.nicho[v];
    return "<tr><td>" + varName(v) + '</td><td class="num">' + fmt(n.p5) + " – " + fmt(n.p95) + " " + n.unidades +
           '</td><td class="num">' + fmt(n.p50) + "</td></tr>";
  }).join("");
  bars();
}

function bars() {
  if (!state.data) return;
  var pct = state.data.resumen.superficie_favorable_pct;
  var box = $("sp-bars"); box.innerHTML = "";
  MONTHS.forEach(function (m, i) {
    var v = pct[i + 1] || 0, b = el("div"); b.style.height = Math.max(2, v) + "%";
    b.title = m + ": " + v + "% of the sea is favourable"; if (i + 1 === state.month) b.className = "on";
    b.addEventListener("click", function () { setMonth(i + 1); });
    box.appendChild(b);
  });
}

function setMonth(m) {
  state.month = m; $("sp-month").value = m;
  $("sp-month-name").textContent = MONTHS[m - 1];
  $("sp-legend-month").textContent = MONTHS[m - 1];
  bars(); if (state.species) loadMap();
}

/* ---------------- map ---------------- */
var canvas = $("sp-map"), geom = null;

function draw() {
  if (state.page !== "species") return;
  var dpr = window.devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return;
  canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
  var g = canvas.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.fillStyle = css("--land"); g.fillRect(0, 0, w, h);

  // Equirectangular, longitude shrunk by cos(38°) so shapes are not stretched.
  var k = Math.cos(38 * Math.PI / 180), spanX = (LON1 - LON0 + STEP) * k, spanY = LAT1 - LAT0 + STEP;
  var s = Math.min((w - 32) / spanX, (h - 32) / spanY), ox = (w - spanX * s) / 2, oy = (h - spanY * s) / 2;
  geom = { s: s, k: k, ox: ox, oy: oy };
  if (!state.map) return;

  var mid = rgb(css("--mid")), hi = rgb(css("--neg")), cw = STEP * k * s, ch = STEP * s;
  state.map.puntos.forEach(function (p) {
    var t = Math.max(0, Math.min(1, p[2]));
    g.fillStyle = "rgb(" + mid.map(function (c, i) { return Math.round(c + (hi[i] - c) * t); }).join(",") + ")";
    g.fillRect(x(p[1]) - cw / 2, y(p[0]) - ch / 2, cw + 0.6, ch + 0.6);
  });
  g.fillStyle = css("--probe");
  state.map.avistamientos.forEach(function (a) {
    g.beginPath(); g.arc(x(a[1]), y(a[0]), Math.min(5, 1.5 + Math.log10(a[2] + 1) * 1.6), 0, 2 * Math.PI); g.fill();
  });
  g.strokeStyle = css("--rule"); g.lineWidth = 1;
  g.strokeRect(x(LON0) - cw / 2, y(LAT1) - ch / 2, spanX * s, spanY * s);
}
function x(lon) { return geom.ox + (lon - LON0 + STEP / 2) * geom.k * geom.s; }
function y(lat) { return geom.oy + (LAT1 - lat + STEP / 2) * geom.s; }
function rgb(h) { h = h.replace("#", ""); if (h.length === 3) h = h.replace(/./g, "$&$&"); return [0, 2, 4].map(function (i) { return parseInt(h.substr(i, 2), 16); }); }

canvas.addEventListener("mousemove", function (ev) {
  if (!state.map || !geom) return;
  var r = canvas.getBoundingClientRect();
  var lon = LON0 - STEP / 2 + (ev.clientX - r.left - geom.ox) / (geom.k * geom.s);
  var lat = LAT1 + STEP / 2 - (ev.clientY - r.top - geom.oy) / geom.s;
  var p = state.map.puntos.filter(function (q) { return Math.abs(q[0] - lat) <= STEP / 2 && Math.abs(q[1] - lon) <= STEP / 2; })[0];
  var seen = state.map.avistamientos.filter(function (a) { return Math.abs(a[0] - lat) <= STEP / 2 && Math.abs(a[1] - lon) <= STEP / 2; })[0];
  if (!p) { $("sp-cell").textContent = "No sea data here."; return; }
  var t = p[0].toFixed(1) + "°N, " + p[1].toFixed(1) + "°E · suitability " + p[2].toFixed(2) + " in " + MONTHS[state.month - 1];
  if (p[2] < state.map.umbral_idoneo && p[3]) t += " · limited by " + varName(p[3]).toLowerCase();
  if (seen) t += " · " + seen[2] + " sightings";
  $("sp-cell").textContent = t;
});

/* ---------------- search, month, start ---------------- */
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
$("sp-month").addEventListener("input", function (ev) { setMonth(Number(ev.target.value)); });

function start() {
  setMonth(state.month);
  loadList().then(function (list) {
    var first = list.filter(function (sp) { return sp.nombre === "Octopus vulgaris"; })[0] || list[0];
    if (first) show(first.nombre);
    else status("No species stored yet. Type a scientific name on the left.");
  }).catch(function (e) { status("The species service is not available right now (" + e.message + ")."); });
}

if (window.ResizeObserver) new ResizeObserver(draw).observe(row.querySelector(".stage"));
if (window.matchMedia) window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", draw);
if (location.hash === "#species") setPage("species");
window.addEventListener("hashchange", function () { setPage(location.hash === "#species" ? "species" : "trends"); });
})();
