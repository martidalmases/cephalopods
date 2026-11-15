import sys, json
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import numpy as np
from scipy import stats
import tendencias as T

def ref(x, t=None):
    """Referencia lenta, escrita aparte: Sen + MK + Hamed-Rao con las reglas del módulo."""
    x = np.asarray(x, float); ok = np.isfinite(x); x = x[ok]; n = len(x)
    t = (np.arange(len(ok)) if t is None else np.asarray(t, float))[ok]
    if n < T.MINIMO: return np.nan, 1.0
    s = 0; sl = []
    for i in range(n - 1):
        for j in range(i + 1, n):
            s += np.sign(x[j] - x[i]); sl.append((x[j] - x[i]) / (t[j] - t[i]))
    b = np.median(sl)
    _, cnt = np.unique(x, return_counts=True)
    var = (n * (n - 1) * (2 * n + 5) - (cnt * (cnt - 1) * (2 * cnt + 5)).sum()) / 18
    r = stats.rankdata(x - b * t); r = r - r.mean()
    lim = stats.norm.ppf(0.975) / np.sqrt(n); sni = 0
    for k in range(1, max(1, n // 4) + 1):
        rho = (r[k:] * r[:-k]).sum() / (r ** 2).sum()
        if abs(rho) > lim: sni += (n - k) * (n - k - 1) * (n - k - 2) * rho
    var *= max(1, 1 + 2 * sni / (n * (n - 1) * (n - 2)))
    z = (s - np.sign(s)) / np.sqrt(var) if var > 0 else 0
    return b * 10, 2 * stats.norm.sf(abs(z))

rng = np.random.default_rng(1)
n, m = 46, 300
Y = rng.normal(size=(n, m)).cumsum(axis=0) * 0.3 + rng.normal(size=(n, m)) + np.linspace(0, 2, n)[:, None] * rng.normal(size=m)
Y[:, :40] = np.round(Y[:, :40])            # empates
Y[rng.integers(0, n, 30), rng.integers(40, 80, 30)] = np.nan   # huecos
Y[:, 299] = 5.0                             # constante
Y[10:, 298] = np.nan                        # demasiado corta
b, p, tau = T.tendencias(Y)
for c in range(m):
    rb, rp = ref(Y[:, c])
    assert np.allclose(b[c], rb, equal_nan=True, atol=1e-10), (c, b[c], rb)
    assert np.allclose(p[c], rp, atol=1e-10), (c, p[c], rp)
print("vectorizado = referencia en", m, "series;", int((p < .05).sum()), "significativas sin ajustar,", int((T.fdr(p) < .05).sum()), "con FDR")
# Sen frente a scipy, tau frente a scipy
for c in (100, 150, 200):
    assert abs(b[c] / 10 - stats.theilslopes(Y[:, c])[0]) < 1e-10
    assert abs(tau[c] - stats.kendalltau(np.arange(n), Y[:, c]).statistic) < 1e-10
# ruido AR(1) sin tendencia: el test corregido debe dar menos falsos positivos que el MK simple
fp_mod = fp_mk = 0; N = 3000
e = rng.normal(size=(n, N)); A = np.zeros((n, N)); A[0] = e[0]
for k in range(1, n): A[k] = 0.6 * A[k - 1] + e[k]
_, pm, _ = T.tendencias(A)
pk = np.array([stats.kendalltau(np.arange(n), A[:, c]).pvalue for c in range(N)])
print("AR(1) rho=0.6 sin tendencia: falsos positivos MK simple %.1f %%, MK modificado %.1f %%" % (100 * (pk < .05).mean(), 100 * (pm < .05).mean()))
W = rng.normal(size=(n, N)); _, pw, _ = T.tendencias(W)
print("ruido blanco: falsos positivos MK modificado %.1f %%" % (100 * (pw < .05).mean()))
# años no consecutivos (ventanas)
t = np.array([1982, 1987, 1992, 1997, 2002, 2007, 2012, 2017, 2022.]); y = 0.03 * t + rng.normal(0, .05, 9)
print("ventanas:", T.tendencia(y, t), ref(y, t))
assert np.allclose(T.tendencia(y, t), ref(y, t))
r = [T.resumen(Y[:, c]) for c in (100, 298, 299)]; T.ajustar(r); print(r)
# vectores para comprobar la versión JavaScript
casos = [Y[:, c].tolist() for c in (0, 5, 45, 60, 100, 150, 200, 250, 299)] + [A[:, c].tolist() for c in range(5)]
json.dump([{"v": [None if not np.isfinite(x) else x for x in v], "slope": None if not np.isfinite(ref(v)[0]) else ref(v)[0], "p": ref(v)[1]} for v in casos], open(__import__("pathlib").Path(__file__).with_name("casos_tendencia.json"), "w"))
