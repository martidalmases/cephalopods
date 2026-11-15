/* Start page: the stripes and the three numbers come from the same data the explorer uses.
   If the backend is not reachable it falls back to the static files in data/. The team block is team.json. */
(function () {
"use strict";
var $ = function (id) { return document.getElementById(id); };
function css(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
function rgb(h) { h = h.replace("#", ""); return [0, 2, 4].map(function (i) { return parseInt(h.substr(i, 2), 16); }); }
function getJSON(p) { return fetch(p).then(function (r) { if (!r.ok) throw new Error(p + " " + r.status); return r.json(); }); }
var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
var tip = $("tip");

function loadSet(dir) { return Promise.all([getJSON(dir + "meta.json"), getJSON(dir + "sst.json")]); }
loadSet("/api/trends/mediterraneo-1980/").catch(function () { return loadSet("data/"); }).then(function (r) {
  var meta = r[0], d = r[1], years = meta.years, v = d.regional.mean, slope = d.regional.slope_per_decade;
  var p = d.regional.p_adj !== undefined ? d.regional.p_adj : d.regional.p, mean = v.reduce(function (a, b) { return a + b; }, 0) / v.length;
  var span = Math.max.apply(null, v.map(function (x) { return Math.abs(x - mean); })) || 1;
  $("stripes-from").textContent = years[0]; $("stripes-to").textContent = years[years.length - 1];
  $("stripes-title").textContent = meta.region.name + " surface temperature, " + years[0] + "–" + years[years.length - 1];
  var cv = $("stripes"), shown = reduced ? years.length : 0, hover = -1;
  function draw() {
    var dpr = window.devicePixelRatio || 1, w = cv.clientWidth, h = cv.clientHeight; cv.width = w * dpr; cv.height = h * dpr;
    var g = cv.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0);
    var mid = rgb(css("--mid")), bw = w / years.length;
    for (var k = 0; k < Math.min(shown, years.length); k++) {
      var t = Math.max(-1, Math.min(1, (v[k] - mean) / span)), to = rgb(css(t < 0 ? "--neg" : "--pos")), a = Math.pow(Math.abs(t), 0.8);
      g.fillStyle = "rgb(" + mid.map(function (c, i) { return Math.round(c + (to[i] - c) * a); }).join(",") + ")";
      g.fillRect(k * bw, 0, bw + 0.6, h);
    }
    if (hover >= 0) { g.strokeStyle = css("--ink"); g.lineWidth = 2; g.strokeRect(hover * bw + 1, 1, bw - 2, h - 2); }
  }
  function reveal() { shown += 1; draw(); if (shown < years.length) setTimeout(reveal, 28); }
  if (reduced) draw(); else setTimeout(reveal, 350);
  var base = "Trend: " + (slope > 0 ? "+" : "−") + Math.abs(slope).toFixed(2) + " °C per decade, " + (p < 0.05 ? "statistically significant" : "not significant") + ". Point at a year.";
  $("stripes-read").textContent = base;
  cv.addEventListener("mousemove", function (e) {
    var rc = cv.getBoundingClientRect(); hover = Math.max(0, Math.min(years.length - 1, Math.floor((e.clientX - rc.left) / rc.width * years.length))); draw();
    $("stripes-read").textContent = years[hover] + ": " + v[hover].toFixed(2) + " °C, " + (v[hover] >= mean ? "+" : "−") + Math.abs(v[hover] - mean).toFixed(2) + " °C against the average of the period";
  });
  cv.addEventListener("mouseleave", function () { hover = -1; draw(); $("stripes-read").textContent = base; });
  if (window.ResizeObserver) new ResizeObserver(draw).observe(cv);

  // First number: counts up to the trend.
  var el = $("fact-trend"), t0 = null, dur = reduced ? 1 : 1100;
  function count(ts) { if (!t0) t0 = ts; var q = Math.min(1, (ts - t0) / dur), e = 1 - Math.pow(1 - q, 3); el.textContent = (slope > 0 ? "+" : "−") + (Math.abs(slope) * e).toFixed(2) + " °C"; if (q < 1) requestAnimationFrame(count); }
  requestAnimationFrame(count);
  $("fact-trend-text").textContent = "per decade at the surface of the " + meta.region.name + ", " + years[0] + "–" + years[years.length - 1] + (p < 0.05 ? " (significant)" : "");
}).catch(function () { $("stripes-read").textContent = "The data are not available right now."; });

getJSON("methodology/results/summary.json").then(function (s) {
  $("fact-vars").textContent = s.final_variables.length;
  $("fact-vars-text").textContent = "variables chosen for the species out of " + s.variables.length + " candidates, all from NASA data";
}).catch(function () {});

getJSON("/api/habitat/especies").then(function (list) {
  if (!list.length) throw new Error("empty");
  $("fact-species").textContent = list.length;
}).catch(function () { $("fact-species").textContent = "Any"; $("fact-species-text").textContent = "marine species in OBIS can be mapped on request"; });

getJSON("team.json").then(function (t) {
  if (t.team) $("team-name").textContent = t.team;
  $("team-intro").textContent = t.intro || "";
  var ul = $("team-members");
  (t.members || []).forEach(function (m) { var li = document.createElement("li"); li.textContent = m.name || ""; if (m.role) { var s = document.createElement("span"); s.textContent = m.role; li.appendChild(s); } ul.appendChild(li); });
  ul.hidden = !(t.members || []).length;
  if (t.photo) { var img = $("team-photo"); img.onload = function () { img.hidden = false; }; img.src = t.photo; }      // stays hidden if the file is not there
}).catch(function () {});
})();
