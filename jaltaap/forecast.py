"""Amazon Chronos-Bolt (open source, Hugging Face) as a second opinion on the river.

GloFAS is a physics model run by Copernicus. Chronos is a pretrained
time-series model from Amazon. When both point the same way we're more sure.
It also lets us do honest backtests: give it data only up to day X and see
what it would have said.
"""
from __future__ import annotations

from functools import lru_cache

import pandas as pd

MODEL_ID = "amazon/chronos-bolt-small"  # ~48M params, runs on a laptop CPU


@lru_cache(maxsize=1)
def _pipeline():
    import torch
    from chronos import BaseChronosPipeline
    return BaseChronosPipeline.from_pretrained(MODEL_ID, device_map="cpu", torch_dtype=torch.float32)


def chronos_forecast(series: pd.Series, horizon: int = 10, context_days: int = 512) -> pd.DataFrame:
    """Returns p10 / p50 / p90 for the next `horizon` days after the series ends."""
    import torch
    ctx = torch.tensor(series.dropna().values[-context_days:], dtype=torch.float32)
    q, _ = _pipeline().predict_quantiles(ctx, prediction_length=horizon, quantile_levels=[0.1, 0.5, 0.9])
    idx = pd.date_range(series.index[-1] + pd.Timedelta(days=1), periods=horizon, freq="D")
    return pd.DataFrame({"p10": q[0, :, 0].numpy(), "p50": q[0, :, 1].numpy(), "p90": q[0, :, 2].numpy()},
                        index=idx)


def days_until(fc: pd.DataFrame, threshold: float, col: str = "p90") -> int | None:
    for i, v in enumerate(fc[col].values):
        if v >= threshold:
            return i + 1
    return None
