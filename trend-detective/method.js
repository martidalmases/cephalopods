/* Method page: draws methodology/variable_selection.md as an article, so the text has one source.
   The small Markdown reader below covers what that file uses: headings, paragraphs, lists, tables,
   images, code, bold, italic and links. The four cards at the top come from results/summary.json. */
(function () {
"use strict";
var BASE = "methodology/";
var $ = function (id) { return document.getElementById(id); };
function esc(s) { return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); }
function slug(s) { return s.toLowerCase().replace(/<[^>]+>/g, "").replace(/[^a-z0-9 -]/g, "").trim().replace(/\s+/g, "-"); }
function href(u) { return /^(https?:|#|mailto:)/.test(u) ? u : BASE + u; }

function inline(s) {
  var code = [];
  s = esc(s).replace(/`([^`]+)`/g, function (m, c) { code.push(c); return "\u0000" + (code.length - 1) + "\u0000"; });
  s = s.replace(/\\\|/g, "|")
       .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, function (m, t, u) { return '<a href="' + href(u) + '">' + t + "</a>"; })
       .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>").replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>")
       .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2">$2</a>');
  return s.replace(/\u0000(\d+)\u0000/g, function (m, i) { return "<code>" + code[+i] + "</code>"; });
}
function cells(line) {                           // a table row; \| is a literal bar
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split(/(?<!\\)\|/).map(function (c) { return c.trim(); });
}
function render(md) {
  var lines = md.replace(/\r/g, "").split("\n"), out = [], i = 0, m;
  function paragraph(start) { var buf = []; while (i < lines.length && lines[i].trim() && !/^(#{1,4} |\||```|!\[|\s*[-*] |\s*\d+\. )/.test(lines[i])) buf.push(lines[i++].trim()); return buf.join(" "); }
  while (i < lines.length) {
    var line = lines[i];
    if (!line.trim()) { i++; continue; }
    if ((m = /^(#{1,4}) (.*)$/.exec(line))) { var t = inline(m[2]), n = m[1].length; out.push("<h" + n + ' id="' + slug(m[2]) + '">' + t + "</h" + n + ">"); i++; continue; }
    if (/^```/.test(line)) { var buf = []; i++; while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]); i++; out.push("<pre><code>" + esc(buf.join("\n")) + "</code></pre>"); continue; }
    if ((m = /^!\[([^\]]*)\]\(([^)]+)\)/.exec(line))) { out.push('<figure><img loading="lazy" src="' + href(m[2]) + '" alt="' + esc(m[1]) + '"><figcaption>' + esc(m[1]) + "</figcaption></figure>"); i++; continue; }
    if (/^\|/.test(line) && i + 1 < lines.length && /^\|[\s:|-]+\|?\s*$/.test(lines[i + 1])) {
      var head = cells(line), rows = []; i += 2;
      while (i < lines.length && /^\|/.test(lines[i])) rows.push(cells(lines[i++]));
      out.push('<div class="tablewrap"><table><thead><tr>' + head.map(function (c) { return "<th>" + inline(c) + "</th>"; }).join("") + "</tr></thead><tbody>" +
        rows.map(function (r) { return "<tr>" + r.map(function (c) { return "<td>" + inline(c) + "</td>"; }).join("") + "</tr>"; }).join("") + "</tbody></table></div>");
      continue;
    }
    if ((m = /^\s*([-*]|\d+\.) /.exec(line))) {
      var ordered = /\d/.test(m[1]), items = [];
      while (i < lines.length && /^\s*([-*]|\d+\.) /.test(lines[i])) {
        var item = lines[i++].replace(/^\s*([-*]|\d+\.) /, "");
        while (i < lines.length && lines[i].trim() && /^\s{2,}\S/.test(lines[i]) && !/^\s*([-*]|\d+\.) /.test(lines[i])) item += " " + lines[i++].trim();
        items.push("<li>" + inline(item) + "</li>");
      }
      out.push((ordered ? "<ol>" : "<ul>") + items.join("") + (ordered ? "</ol>" : "</ul>")); continue;
    }
    out.push("<p>" + inline(paragraph()) + "</p>");
  }
  return out.join("\n");
}

fetch(BASE + "variable_selection.md").then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); }).then(function (md) {
  var art = $("article"); art.innerHTML = render(md);
  var h1 = art.querySelector("h1"); if (h1) $("title").textContent = h1.textContent;
  var toc = $("toc"), links = [];
  art.querySelectorAll("h2, h3").forEach(function (h) { var a = document.createElement("a"); a.href = "#" + h.id; a.textContent = h.textContent; if (h.tagName === "H3") a.className = "sub"; toc.appendChild(a); links.push([h, a]); });
  if (window.IntersectionObserver) {
    var io = new IntersectionObserver(function (es) { es.forEach(function (e) { if (e.isIntersecting) links.forEach(function (l) { l[1].classList.toggle("on", l[0] === e.target); }); }); }, { rootMargin: "-70px 0px -70% 0px" });
    links.forEach(function (l) { io.observe(l[0]); });
  }
  if (location.hash) { var target = document.getElementById(location.hash.slice(1)); if (target) target.scrollIntoView(); }
}).catch(function (e) { $("article").innerHTML = '<p>The article could not be loaded (' + esc(String(e.message)) + '). It is the file <a href="' + BASE + 'variable_selection.md">methodology/variable_selection.md</a>.</p>'; });

fetch(BASE + "results/summary.json").then(function (r) { return r.json(); }).then(function (s) {
  var by = {}; s.variables.forEach(function (v) { by[v.id] = v; });
  $("chosen").innerHTML = s.final_variables.map(function (id) {
    var v = by[id], t = v.trend, sig = t.basin_p < 0.05;
    return "<div><span class=\"src\">" + esc(v.source) + "</span><b>" + esc(v.name) + "</b><span class=\"num\">" + (t.basin_slope_per_decade > 0 ? "+" : "−") + Math.abs(t.basin_slope_per_decade).toFixed(Math.abs(t.basin_slope_per_decade) < 0.1 ? 3 : 2) + " " + esc(v.unit) +
      " per decade" + (sig ? "" : " (not significant)") + " · " + Math.round(t.pct_cells_significant) + " % of cells</span><span class=\"why\">" + esc(v.reason) + "</span></div>";
  }).join("");
  var counts = {}; s.variables.forEach(function (v) { counts[v.decision] = (counts[v.decision] || 0) + 1; });
  $("funnel").textContent = s.variables.length + " candidates → " + s.final_variables.length + " chosen · study on the " + s.region + ", " + s.period + " · " +
    Object.keys(counts).filter(function (k) { return k !== "final"; }).map(function (k) { return counts[k] + " " + k.replace(/_/g, " "); }).join(", ");
}).catch(function () { $("chosen").hidden = true; });
})();
