const S = require("../../trend-detective/stats.js");
const casos = require("./casos_tendencia.json");
let worst = 0;
for (const c of casos) {
  const r = S.sen(c.v);
  if (c.slope === null) { if (!isNaN(r.slope) || r.p !== 1) throw new Error("corta"); continue; }
  const e = Math.max(Math.abs(r.slope - c.slope), Math.abs(r.p - c.p)); worst = Math.max(worst, e);
  if (e > 2e-7) throw new Error("difiere " + JSON.stringify([r, c.slope, c.p]));
}
console.log(casos.length, "casos; mayor diferencia JS-Python", worst.toExponential(2));
console.log("fdr", S.fdr([0.01, 0.04, null, 0.03, 0.5]));
