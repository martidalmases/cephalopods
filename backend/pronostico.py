"""On-demand annual forecasts for the Trends analysis chart."""

from __future__ import annotations

import math

import numpy as np

HORIZON = 5
MIN_OBSERVATIONS = 8
MAX_INTERPOLATED_GAP = 2


def pronosticar(anios: list[int], valores: list[float | None]) -> dict:
    if len(anios) != len(valores) or len(anios) < MIN_OBSERVATIONS:
        raise ValueError(f"At least {MIN_OBSERVATIONS} years and one value per year are required.")
    if any(b != a + 1 for a, b in zip(anios, anios[1:])):
        raise ValueError("Years must be consecutive.")
    serie = [None if v is None else float(v) for v in valores]
    if any(v is not None and not math.isfinite(v) for v in serie):
        raise ValueError("The series contains non-finite values.")
    validos = [i for i, v in enumerate(serie) if v is not None]
    if len(validos) < MIN_OBSERVATIONS:
        raise ValueError(f"At least {MIN_OBSERVATIONS} observed years are required.")
    primero, ultimo = validos[0], validos[-1]
    serie, anios = serie[primero:ultimo + 1], anios[primero:ultimo + 1]
    rellenados, i = [], 0
    while i < len(serie):
        if serie[i] is not None:
            i += 1
            continue
        inicio = i
        while i < len(serie) and serie[i] is None:
            i += 1
        hueco = i - inicio
        if hueco > MAX_INTERPOLATED_GAP:
            raise ValueError("A gap longer than two years prevents a reliable forecast.")
        izquierda, derecha = serie[inicio - 1], serie[i]
        for j in range(hueco):
            serie[inicio + j] = izquierda + (derecha - izquierda) * (j + 1) / (hueco + 1)
            rellenados.append(anios[inicio + j])
    # Annual means have no within-year seasonality and only 23–46 observations.
    from darts import TimeSeries
    from darts.models import ExponentialSmoothing
    from darts.utils.utils import ModelMode, SeasonalityMode
    ts = TimeSeries.from_values(np.asarray(serie, dtype=float))
    model = ExponentialSmoothing(trend=ModelMode.ADDITIVE, damped=True,
                                 seasonal=SeasonalityMode.NONE, random_state=0)
    model.fit(ts)
    draws = model.predict(HORIZON, num_samples=500)
    quantiles = draws.quantile([0.05, 0.5, 0.95]).values()
    forecast_years = list(range(anios[-1] + 1, anios[-1] + HORIZON + 1))
    return {
        "years": forecast_years,
        "lower": [float(x) for x in quantiles[:, 0]],
        "median": [float(x) for x in quantiles[:, 1]],
        "upper": [float(x) for x in quantiles[:, 2]],
        "last_observed_year": int(anios[-1]),
        "observations": len(validos),
        "interpolated_years": rellenados,
        "model": "Damped additive exponential smoothing (Darts)",
    }
