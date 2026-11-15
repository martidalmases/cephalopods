"""
Tendencia y significación, el MISMO método para todo el proyecto (variables y especies):

  1. Pendiente de Sen (Theil-Sen): mediana de las pendientes entre todos los pares de años.
  2. Test de Mann-Kendall modificado (Hamed y Rao, 1998): el test de Mann-Kendall con la
     varianza de S corregida por la autocorrelación de la serie. Dos años seguidos se parecen,
     así que hay menos información independiente de la que aparenta el número de años.
  3. Corrección de Benjamini-Hochberg (FDR) cuando se hacen muchos tests a la vez (uno por
     celda del mapa, o uno por indicador de una especie).

Detalles de la corrección de Hamed y Rao tal como se aplica aquí:
  - se quita la tendencia de Sen, se pasan los residuos a rangos y se calcula su autocorrelación;
  - sólo cuentan los retardos 1..n/4 cuya autocorrelación es significativa al 5 %;
  - la corrección sólo puede hacer el test MÁS estricto (factor >= 1): una autocorrelación
    negativa no se usa para ganar significación.

Sólo usa numpy y scipy. La web (trend-detective/app.js) tiene la misma función para una serie.

    slope, p = tendencia(serie)                 # una serie (admite NaN)
    slope, p, tau = tendencias(valores)         # (años, celdas): una serie por columna
    q = fdr(p)                                  # p ajustadas, mismo tamaño
"""

from __future__ import annotations

import numpy as np
from scipy import stats

ALFA = 0.05
MINIMO = 8            # años (o ventanas) con dato para dar una tendencia
METODO = "Sen slope, modified Mann-Kendall (Hamed-Rao), Benjamini-Hochberg FDR"


def _rangos(x: np.ndarray) -> np.ndarray:
    """Rangos por columna (empates: rango medio)."""
    return stats.rankdata(x, axis=0)


def _bloque(y: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Series completas (n, m), todas con los mismos tiempos t. Devuelve pendiente, p, tau."""
    n, m = y.shape
    i, j = np.triu_indices(n, 1)
    dif = y[j] - y[i]                                    # (pares, m)
    s = np.sign(dif).sum(axis=0)
    pendiente = np.median(dif / (t[j] - t[i])[:, None], axis=0)

    # Varianza de S con empates: cada valor repetido c veces aporta (c-1)(2c+5).
    empates = np.zeros(m)
    for k in range(n):
        c = (y == y[k]).sum(axis=0)
        empates += (c - 1) * (2 * c + 5)
    var = (n * (n - 1) * (2 * n + 5) - empates) / 18.0

    # Hamed-Rao: autocorrelación de los rangos de la serie sin tendencia.
    r = _rangos(y - pendiente[None, :] * t[:, None])
    r = r - r.mean(axis=0)
    den = (r ** 2).sum(axis=0)
    limite = stats.norm.ppf(1 - ALFA / 2) / np.sqrt(n)
    suma = np.zeros(m)
    for k in range(1, max(1, n // 4) + 1):
        with np.errstate(invalid="ignore", divide="ignore"):
            rho = np.where(den > 0, (r[k:] * r[:-k]).sum(axis=0) / den, 0.0)
        suma += np.where(np.abs(rho) > limite, (n - k) * (n - k - 1) * (n - k - 2) * rho, 0.0)
    factor = np.maximum(1.0, 1 + 2 * suma / (n * (n - 1) * (n - 2)))
    var = var * factor

    with np.errstate(invalid="ignore", divide="ignore"):
        z = np.where(var > 0, (s - np.sign(s)) / np.sqrt(var), 0.0)
    p = 2 * stats.norm.sf(np.abs(z))
    tau = s / (0.5 * n * (n - 1))
    return pendiente, p, tau


def tendencias(valores: np.ndarray, tiempos: np.ndarray | None = None, por_decada: bool = True,
               trozo: int = 4000) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Pendiente de Sen, p (Mann-Kendall modificado) y tau de Kendall de cada columna de
    `valores` (tiempo, series). `tiempos` son los años (por defecto 0, 1, 2...). Las columnas
    con huecos se calculan con los años que tienen; con menos de MINIMO dan NaN y p = 1.
    """
    valores = np.asarray(valores, dtype=float)
    if valores.ndim == 1:
        valores = valores[:, None]
    n, m = valores.shape
    t = np.arange(n, dtype=float) if tiempos is None else np.asarray(tiempos, dtype=float)
    pendiente, p, tau = np.full(m, np.nan), np.ones(m), np.full(m, np.nan)
    hay = np.isfinite(valores)
    completas = np.nonzero(hay.all(axis=0))[0]
    if n >= MINIMO:
        for a in range(0, len(completas), trozo):
            cols = completas[a:a + trozo]
            pendiente[cols], p[cols], tau[cols] = _bloque(valores[:, cols], t)
    for c in np.nonzero(~hay.all(axis=0) & (hay.sum(axis=0) >= MINIMO))[0]:
        ok = hay[:, c]
        b, pp, ta = _bloque(valores[ok, c][:, None], t[ok])
        pendiente[c], p[c], tau[c] = b[0], pp[0], ta[0]
    if por_decada:
        pendiente = pendiente * 10
    return pendiente, p, tau


def tendencia(serie, tiempos=None, por_decada: bool = True) -> tuple[float, float]:
    """Pendiente de Sen y p de una sola serie."""
    b, p, _ = tendencias(np.asarray(serie, dtype=float)[:, None], tiempos, por_decada)
    return float(b[0]), float(p[0])


def fdr(p: np.ndarray) -> np.ndarray:
    """p ajustadas de Benjamini-Hochberg. Los NaN no cuentan como test y se devuelven como NaN."""
    p = np.asarray(p, dtype=float)
    q = np.full(p.shape, np.nan)
    ok = np.isfinite(p)
    if ok.any():
        q[ok] = stats.false_discovery_control(p[ok], method="bh")
    return q


def recta(serie, tiempos, pendiente_decada: float) -> tuple[float, float]:
    """Recta de Sen para dibujar: (valor en el primer tiempo, valor en el último)."""
    y, t = np.asarray(serie, dtype=float), np.asarray(tiempos, dtype=float)
    ok = np.isfinite(y)
    if not ok.any() or not np.isfinite(pendiente_decada):
        return float("nan"), float("nan")
    b = pendiente_decada / 10
    corte = np.median(y[ok] - b * t[ok])
    return float(corte + b * t[0]), float(corte + b * t[-1])


def resumen(serie, tiempos=None, decimales: int = 3) -> dict:
    """Tendencia de una serie lista para JSON (p sin ajustar; `p_adj` se añade con ajustar())."""
    y = np.asarray(serie, dtype=float)
    b, p, tau = tendencias(y[:, None], tiempos)
    n = int(np.isfinite(y).sum())
    if not np.isfinite(b[0]):
        return {"n": n, "pendiente_decada": None, "p": None, "tau": None}
    return {"n": n, "pendiente_decada": round(float(b[0]), decimales + 1), "p": round(float(p[0]), 5),
            "tau": round(float(tau[0]), 3)}


def ajustar(resumenes: list[dict]) -> None:
    """Añade `p_adj` (FDR sobre la familia de tests) y `significativa` a cada resumen."""
    p = np.array([r["p"] if r.get("p") is not None else np.nan for r in resumenes], dtype=float)
    q = fdr(p)
    for r, v in zip(resumenes, q):
        r["p_adj"] = None if not np.isfinite(v) else round(float(v), 5)
        r["significativa"] = bool(np.isfinite(v) and v < ALFA)
