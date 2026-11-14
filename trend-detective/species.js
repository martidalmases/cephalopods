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
// Map window: the whole ocean (the polar rows are cut, they have almost no data). The cell size
// comes from the backend (paso_grados, 1° by default).
var LON0 = -180, LON1 = 180, LAT0 = -78, LAT1 = 84, STEP = 1;

var $ = function (id) { return document.getElementById(id); };
function el(tag, cls, txt) { var e = document.createElement(tag); if (cls) e.className = cls; if (txt !== undefined) e.textContent = txt; return e; }
function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
function commonName(sp) { return COMMON_NAMES[sp.nombre] || sp.nombre_comun || ""; }
function varName(v) { return VARIABLE_NAMES[v] || v; }

var state = { page: "trends", species: null, year: null, data: null, map: null, list: [] };

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
  '<section><h2>Region</h2><p class="fact">World ocean</p><p class="fact dim" id="sp-period">&nbsp;</p></section>' +
  '<section><h2>Species</h2><p class="fact dim">Stored species. Cephalopods are always ready.</p><ul class="varlist" id="sp-list"></ul></section>' +
  '<section class="sp-search"><h2>Find another species</h2>' +
  '<input id="sp-find" list="sp-suggest" placeholder="Scientific name, e.g. Thunnus thynnus" autocomplete="off" aria-label="Scientific name">' +
  '<datalist id="sp-suggest"></datalist><p class="fact dim">Computed on first request, then stored.</p></section>' +
  '<section class="sp-month"><h2>Year</h2><p class="fact" id="sp-year-name">&nbsp;</p>' +
  '<input type="range" id="sp-year" min="1980" max="2025" step="1" aria-label="Year"></section>' +
  '<section><h2>What is this?</h2><p class="fact dim">Each sighting (OBIS, since 1980) is matched to the sea conditions of its place and year ' +
  '(monthly data averaged per year). Habitat suitability (0 to 1) says how close each place and year is to the conditions where the species has been seen. ' +
  'It is a statistical match, not proof of cause.</p></section>';
trendSide.parentNode.appendChild(side);

var trendRow = $("row");
var row = el("div", "row"); row.id = "sp-row"; row.hidden = true;
row.innerHTML =
  '<main class="main"><div class="legend" id="sp-legend"></div>' +
  '<div class="stage"><canvas id="sp-map" aria-label="Habitat suitability map"></canvas><p class="loading" id="sp-status">Loading…</p></div></main>' +
  '<aside class="panel" aria-label="Species details"><div class="sp-panel">' +
  '<div><div class="panel-head"><h2 id="sp-title">&nbsp;</h2></div><p class="fact dim" id="sp-sub">&nbsp;</p></div>' +
  '<section><h2>Favourable sea area by year</h2><div class="sp-bars" id="sp-bars"></div><div class="sp-years" id="sp-years"></div>' +
  '<p class="fact dim" id="sp-trend">&nbsp;</p></section>' +
  '<section><h2>What limits its habitat</h2><ul class="sp-limits" id="sp-limits"></ul></section>' +
  '<section><h2>Conditions where it is seen</h2><table class="allvars"><thead><tr><th>Variable</th><th>Typical range</th><th>Median</th></tr></thead><tbody id="sp-niche"></tbody></table>' +
  '<p class="note">Typical range: 5th to 95th percentile of the conditions at its sightings. ' +
  'Variables marked * cover every year and make the yearly suitability; the others start later (satellites) and only describe the niche.</p></section>' +
  '<section><h2>Place</h2><p class="fact dim sp-cell" id="sp-cell">Point at the map to read a place.</p></section>' +
  '</div></aside>';
trendRow.parentNode.appendChild(row);

$("sp-legend").innerHTML = '<span>Habitat suitability: 0 <span class="ramp"></span> 1</span>' +
  '<span><span class="sp-dot"></span>sightings that year</span><span><span class="sp-dot faint"></span>other years</span>' +
  '<span id="sp-legend-year"></span>';

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
  status("Computing " + name + "… The first request for a species can take up to a minute (its sightings are downloaded from OBIS).");
  return api("/especies/" + encodeURIComponent(name)).then(function (d) {
    if (state.species !== name) return;
    state.data = d; state.species = d.nombre;
    var years = d.anios, slider = $("sp-year");
    slider.min = years[0]; slider.max = years[years.length - 1];
    if (!state.year || years.indexOf(state.year) < 0) state.year = years[years.length - 1];
    setYear(state.year, true);
    panel(); return loadList(d.nombre).then(loadMap);
  }).catch(function (e) { status("Could not compute " + name + ": " + e.message); });
}

function loadMap() {
  var name = state.species, year = state.year;
  return api("/especies/" + encodeURIComponent(name) + "/mapa?anio=" + year).then(function (m) {
    if (state.species !== name || state.year !== year) return;
    STEP = m.paso_grados || STEP;
    m.celda = {};   // "lat,lon" -> point, for the hover read-out
    m.puntos.forEach(function (p) { m.celda[p[0] + "," + p[1]] = p; });
    m.vistos = {};
    m.avistamientos.forEach(function (a) { m.vistos[a[0] + "," + a[1]] = a; });
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
  $("sp-period").textContent = "Yearly results " + r.periodo[0] + " to " + r.periodo[1];
  var t = r.tendencia_pp_por_decada;
  $("sp-trend").textContent = t === null ? "" : "Trend: " + (t > 0 ? "+" : t < 0 ? "−" : "") + Math.abs(t).toFixed(1) +
    " percentage points of favourable sea per decade (" + r.periodo[0] + "–" + r.periodo[1] + ", least-squares line).";

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
  years.forEach(function (y) {
    var v = pct[y] || 0, b = el("div"); b.style.height = Math.max(2, v) + "%";
    b.title = y + ": " + v + "% of the sea is favourable"; if (y === state.year) b.className = "on";
    b.addEventListener("click", function () { setYear(y); });
    box.appendChild(b);
  });
}

function setYear(y, quiet) {
  state.year = y; $("sp-year").value = y;
  var seen = state.data && state.data.resumen.avistamientos_por_anio[y];
  $("sp-year-name").textContent = y + (seen ? " · " + seen + " sightings" : "");
  $("sp-legend-year").textContent = String(y);
  if (!quiet) { bars(); if (state.species) loadMap(); }
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

  // Equirectangular world map (plate carrée).
  var k = 1, spanX = LON1 - LON0, spanY = LAT1 - LAT0;
  var s = Math.min((w - 32) / spanX, (h - 32) / spanY), ox = (w - spanX * s) / 2, oy = (h - spanY * s) / 2;
  geom = { s: s, k: k, ox: ox, oy: oy };
  if (!state.map) return;

  var mid = rgb(css("--mid")), hi = rgb(css("--neg")), cw = STEP * k * s, ch = STEP * s;
  state.map.puntos.forEach(function (p) {
    var t = Math.max(0, Math.min(1, p[2]));
    g.fillStyle = "rgb(" + mid.map(function (c, i) { return Math.round(c + (hi[i] - c) * t); }).join(",") + ")";
    g.fillRect(x(p[1]) - cw / 2, y(p[0]) - ch / 2, cw + 0.6, ch + 0.6);
  });
  // Sightings: other years small and faint, the chosen year on top.
  var probe = css("--probe");
  g.fillStyle = probe; g.globalAlpha = 0.35;
  state.map.avistamientos.forEach(function (a) { if (!a[3]) { g.beginPath(); g.arc(x(a[1]), y(a[0]), 1.6, 0, 2 * Math.PI); g.fill(); } });
  g.globalAlpha = 1;
  state.map.avistamientos.forEach(function (a) {
    if (a[3]) { g.beginPath(); g.arc(x(a[1]), y(a[0]), Math.min(6, 2.5 + Math.log10(a[3] + 1) * 2), 0, 2 * Math.PI); g.fill(); }
  });
  g.strokeStyle = css("--rule"); g.lineWidth = 1;
  g.strokeRect(geom.ox, geom.oy, spanX * s, spanY * s);
}
function x(lon) { return geom.ox + (lon - LON0) * geom.k * geom.s; }
function y(lat) { return geom.oy + (LAT1 - lat) * geom.s; }
function rgb(h) { h = h.replace("#", ""); if (h.length === 3) h = h.replace(/./g, "$&$&"); return [0, 2, 4].map(function (i) { return parseInt(h.substr(i, 2), 16); }); }

canvas.addEventListener("mousemove", function (ev) {
  if (!state.map || !geom) return;
  var r = canvas.getBoundingClientRect();
  var lon = LON0 + (ev.clientX - r.left - geom.ox) / (geom.k * geom.s);
  var lat = LAT1 - (ev.clientY - r.top - geom.oy) / geom.s;
  // Centre of the cell under the pointer, written like the backend writes it.
  var cLat = Math.floor(lat / STEP) * STEP + STEP / 2, cLon = Math.floor(lon / STEP) * STEP + STEP / 2;
  var key = cLat + "," + cLon, p = state.map.celda[key], seen = state.map.vistos[key];
  if (!p) { $("sp-cell").textContent = "No sea data here."; return; }
  var ns = function (v, a, b) { return Math.abs(v).toFixed(1) + "°" + (v >= 0 ? a : b); };
  var t = ns(p[0], "N", "S") + ", " + ns(p[1], "E", "W") + " · suitability " + p[2].toFixed(2) + " in " + state.year;
  if (p[2] < state.map.umbral_idoneo && p[3] >= 0) t += " · limited by " + varName(state.map.limitantes[p[3]]).toLowerCase();
  if (seen) t += " · " + seen[3] + " sightings that year, " + seen[2] + " in all years";
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
$("sp-year").addEventListener("input", function (ev) { setYear(Number(ev.target.value)); });

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
