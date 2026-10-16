"""Еженедельное переобучение на ТЕХ ЖЕ признаках, что считает боевое ядро.

Исходный MarketMlOptimizer учил модель на средних приращениях цены и объёма
минутных свечей, а веса применялись к дисбалансу стакана и смещению
микроцены — это разные величины, коэффициенты не имели смысла. Теперь ядро
само пишет (ts, imbalance, bias, mid, spread) каждые rec_interval секунд в
преаллоцированный массив, сессия сохраняется в .npz, а обучение идёт по этим
файлам: обучающие и боевые признаки считаются одним и тем же кодом.

Метка: направление mid через `horizon` секунд (нулевые движения отброшены).
Walk-forward: TimeSeriesSplit с gap = горизонт метки (без утечки будущего).
Метрика — не accuracy по всем точкам, а hit-rate там, где правило реально
торгует (p > p_buy или p < p_sell), при минимальном покрытии.
"""

from __future__ import annotations

import glob
import json
import logging
import os
from datetime import datetime

import numpy as np

from . import kernels as K

logger = logging.getLogger("ContestRobot")


def session_rows(rec: np.ndarray, n: int) -> np.ndarray:
    """Записанные строки кольца rec в хронологическом порядке."""
    cap = rec.shape[0]
    if n <= cap:
        return rec[:n].copy()
    head = n % cap
    return np.concatenate((rec[head:], rec[:head]))


def save_session(rec: np.ndarray, n: int, feature_dir: str, day: datetime) -> str | None:
    rows = session_rows(rec, n)
    if len(rows) == 0:
        return None
    os.makedirs(feature_dir, exist_ok=True)
    base = os.path.join(feature_dir, day.strftime("%Y-%m-%d"))
    path = base + ".npz"
    k = 1
    while os.path.exists(path):
        path = f"{base}_{k}.npz"
        k += 1
    np.savez_compressed(path, rows=rows)
    return path


def load_weights(path: str) -> dict | None:
    try:
        with open(path) as f:
            data = json.load(f)
        return data if data.get("status") == "APPROVED" else None
    except (OSError, ValueError):
        return None


def save_weights(path: str, result: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


class MarketMlOptimizer:
    def __init__(self, feature_dir: str, *, horizon: float = 5.0, lookback_files: int = 10,
                 rec_interval: float = 0.25, p_buy: float = 0.65, p_sell: float = 0.35,
                 min_samples: int = 2_000, min_coverage: float = 0.02, min_hit_rate: float = 0.53) -> None:
        self.feature_dir = feature_dir
        self.horizon = horizon
        self.lookback_files = lookback_files
        self.rec_interval = rec_interval
        self.p_buy = p_buy
        self.p_sell = p_sell
        self.min_samples = min_samples
        self.min_coverage = min_coverage
        self.min_hit_rate = min_hit_rate

    def _label(self, rows: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        ts = rows[:, K.REC_TS]
        mid = rows[:, K.REC_MID]
        j = np.searchsorted(ts, ts + self.horizon)
        valid = j < len(ts)
        idx = np.nonzero(valid)[0]
        dy = mid[j[idx]] - mid[idx]
        moved = dy != 0.0
        idx = idx[moved]
        X = np.column_stack((rows[idx, K.REC_IMB], rows[idx, K.REC_BIAS]))
        return X, (dy[moved] > 0.0).astype(np.int64)

    def prepare_features(self) -> tuple[np.ndarray, np.ndarray]:
        files = sorted(glob.glob(os.path.join(self.feature_dir, "*.npz")))[-self.lookback_files:]
        xs, ys = [], []
        for path in files:  # метки внутри файла: не склеиваем ночной гэп
            rows = np.load(path)["rows"]
            if len(rows) > 1:
                X, y = self._label(rows)
                xs.append(X)
                ys.append(y)
        if not xs:
            return np.empty((0, 2)), np.empty(0, dtype=np.int64)
        return np.vstack(xs), np.concatenate(ys)

    def _evaluate(self, y_true: np.ndarray, proba: np.ndarray) -> tuple[float, float, int]:
        long_ = proba > self.p_buy
        short = proba < self.p_sell
        n = int(long_.sum() + short.sum())
        if n == 0:
            return 0.0, 0.0, 0
        hits = int((y_true[long_] == 1).sum() + (y_true[short] == 0).sum())
        return hits / n, n / len(y_true), n

    def train_and_optimize(self) -> dict:
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import TimeSeriesSplit

        try:
            X, y = self.prepare_features()
            if len(y) < self.min_samples:
                logger.warning("[ML REJECTED] Мало данных: %d < %d", len(y), self.min_samples)
                return {"status": "REJECTED", "reason": "not_enough_data", "samples": int(len(y))}
            gap = int(self.horizon / self.rec_interval) + 1
            tscv = TimeSeriesSplit(n_splits=5, gap=gap)
            best = None
            for c in (0.001, 0.01, 0.1, 1.0, 10.0):
                hits = trades = 0
                covs = []
                for train_idx, test_idx in tscv.split(X):
                    if len(np.unique(y[train_idx])) < 2:
                        continue
                    model = LogisticRegression(C=c, solver="lbfgs")
                    model.fit(X[train_idx], y[train_idx])
                    hr, cov, n = self._evaluate(y[test_idx], model.predict_proba(X[test_idx])[:, 1])
                    hits += hr * n
                    trades += n
                    covs.append(cov)
                if not covs:
                    continue
                hit_rate = hits / trades if trades else 0.0
                coverage = float(np.mean(covs))
                if coverage >= self.min_coverage and (best is None or hit_rate > best[1]):
                    best = (c, hit_rate, coverage, trades)

            if best is None or best[1] <= self.min_hit_rate:
                hr = best[1] if best else 0.0
                logger.warning("[ML REJECTED] Нет преимущества на walk-forward: hit-rate %.4f", hr)
                return {"status": "REJECTED", "reason": "no_edge", "hit_rate": hr}

            c, hit_rate, coverage, trades = best
            final = LogisticRegression(C=c, solver="lbfgs").fit(X, y)
            logger.info("[ML APPROVED] hit-rate %.4f, покрытие %.3f, C=%s", hit_rate, coverage, c)
            return {
                "status": "APPROVED",
                "weights": final.coef_.tolist(),
                "intercept": float(final.intercept_[0]),
                "hit_rate": hit_rate,
                "coverage": coverage,
                "oos_trades": int(trades),
                "C": c,
                "samples": int(len(y)),
                "trained_at": datetime.now().isoformat(timespec="seconds"),
            }
        except Exception as e:
            logger.error("[ML ERR] Ошибка обучения: %s", e)
            return {"status": "ERROR", "error": repr(e)}
