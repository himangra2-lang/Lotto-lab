"""Lotto Lab Prediction Arena.

Every model outputs, for the next draw, a probability for each number (summing to
the count of numbers drawn) and a pick of the k most likely numbers.

This script:
  1. Walk-forward backtests every model on past draws (each prediction uses only
     draws before it) and scores it against the pure-random baseline.
  2. Scores live predictions made on earlier runs once their draw has happened.
  3. Pre-registers each model's prediction for the next draw. Because the GitHub
     Action commits it, the git history proves it existed before the draw.

Output: docs/data/arena/<game>.json
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from games import GAMES  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent.parent / "docs" / "data"
ARENA_DIR = DATA_DIR / "arena"
MIN_HISTORY = 50        # draws of the same format a model needs before predicting
BACKTEST_TARGETS = 400  # most recent draws used for the backtest
EPS = 1e-4


# ---------------------------------------------------------------- models
# Each model: f(history: np.ndarray[T, N] of 0/1 hits, k, N) -> probabilities[N]

def m_random(H, k, N):
    return np.full(N, k / N)


def m_hot(H, k, N, window=100):
    c = H[-window:].sum(0) + 1.0
    return c / c.sum() * k


def m_cold(H, k, N, window=100):
    c = H[-window:].sum(0) + 1.0
    inv = 1.0 / c
    return inv / inv.sum() * k


def gaps(H):
    T, N = H.shape
    g = np.full(N, T, dtype=float)
    for n in range(N):
        idx = np.flatnonzero(H[:, n])
        if idx.size:
            g[n] = T - 1 - idx[-1]
    return g


def m_overdue(H, k, N):
    w = np.log1p(gaps(H)) + 1.0
    return w / w.sum() * k


def m_bayes(H, k, N, prior_draws=200):
    """Bias hunter: Beta-Binomial posterior per number, shrunk hard towards fair.

    A fair draw stays near k/N. A real, persistent bias in the machine would pull
    a number away from k/N as evidence accumulates. Uses the whole format era.
    """
    T = H.shape[0]
    a = prior_draws * k / N
    p = (H.sum(0) + a) / (T + prior_draws)
    return p / p.sum() * k


def m_markov(H, k, N, window=400, alpha=1.0):
    """P(number j next | numbers drawn last time), from transition counts."""
    X = H[-window:]
    if X.shape[0] < 2:
        return m_random(H, k, N)
    trans = X[:-1].T @ X[1:] + alpha           # N x N co-occurrence across consecutive draws
    trans = trans / trans.sum(1, keepdims=True)
    last = np.flatnonzero(H[-1])
    s = trans[last].mean(0)
    return s / s.sum() * k


def features(H, N):
    T = H.shape[0]
    f = np.column_stack([
        H[-10:].mean(0), H[-50:].mean(0), H[-100:].mean(0),
        np.minimum(gaps(H), 100) / 100.0, H[-1], np.arange(1, N + 1) / N,
    ])
    return f


class GBM:
    """Gradient-boosted trees on per-number features, retrained every 25 draws."""

    def __init__(self):
        self.model = None
        self.trained_at = -999

    def __call__(self, H, k, N):
        from sklearn.ensemble import HistGradientBoostingClassifier
        T = H.shape[0]
        if self.model is None or T - self.trained_at >= 25:
            Xs, ys = [], []
            for t in range(max(50, T - 300), T):
                Xs.append(features(H[:t], N))
                ys.append(H[t])
            X, y = np.vstack(Xs), np.concatenate(ys)
            self.model = HistGradientBoostingClassifier(max_iter=120, learning_rate=0.05, max_depth=3,
                                                        l2_regularization=1.0, random_state=0)
            self.model.fit(X, y)
            self.trained_at = T
        p = self.model.predict_proba(features(H, N))[:, 1]
        return p / p.sum() * k


MODELS = {
    "random": ("Pure random", "Every number equally likely. The baseline every model must beat.", lambda: m_random),
    "hot": ("Hot numbers", "Favours numbers drawn most often in the last 100 draws.", lambda: m_hot),
    "cold": ("Cold numbers", "Favours numbers drawn least often in the last 100 draws.", lambda: m_cold),
    "overdue": ("Overdue", "Favours numbers that haven't appeared for the longest time.", lambda: m_overdue),
    "bayes": ("Bias hunter", "Bayesian estimate of each number's true rate, shrunk towards fair. Catches a real machine bias if one exists.", lambda: m_bayes),
    "markov": ("Markov chain", "Learns which numbers tend to follow last draw's numbers.", lambda: m_markov),
    "gbm": ("Machine learning", "Gradient-boosted trees on recent frequency, gaps and repeats, retrained every 25 draws.", lambda: GBM()),
}


# ---------------------------------------------------------------- scoring

def clean(p, k):
    p = np.clip(p, EPS, 1 - EPS)
    return p / p.sum() * k


def loglik(p, y):
    return float(np.sum(y * np.log(p) + (1 - y) * np.log(1 - p)))


RNG = np.random.default_rng()


def pick_top(p, k):
    # A flat distribution has no "top": draw a random line instead of 1..k.
    if np.ptp(p) < 1e-9:
        return sorted(int(i) + 1 for i in RNG.choice(len(p), k, replace=False))
    return sorted(int(i) + 1 for i in np.argsort(-p, kind="stable")[:k])


def summarize(rows, k, N):
    """rows: list of (matches, ll_model - ll_random)."""
    n = len(rows)
    if not n:
        return {"n": 0}
    m = np.array([r[0] for r in rows], float)
    d = np.array([r[1] for r in rows], float)
    mu = k * k / N
    var = k * (k / N) * ((N - k) / N) * ((N - k) / (N - 1))
    zm = (m.mean() - mu) / math.sqrt(var / n)
    sd = d.std(ddof=1) if n > 1 else 0.0
    zl = d.mean() / (sd / math.sqrt(n)) if sd > 0 else 0.0
    p = lambda z: math.erfc(abs(z) / math.sqrt(2))
    return {"n": n, "avgMatches": round(m.mean(), 4), "expected": round(mu, 4),
            "matchP": round(p(zm), 4), "bitsPerDraw": round(d.mean() / math.log(2), 5),
            "bitsP": round(p(zl), 4), "best": int(m.max()),
            "threePlus": int((m >= 3).sum())}


# ---------------------------------------------------------------- data

def load_draws(key):
    path = DATA_DIR / f"{key}.json"
    if not path.exists():
        return []
    return json.loads(path.read_text())["draws"]


def era_matrix(draws, era):
    rows = [d for d in draws if d["era"] == era]
    N = int(era.split("/")[1])
    H = np.zeros((len(rows), N), dtype=np.float64)
    for t, d in enumerate(rows):
        H[t, np.array(d["main"]) - 1] = 1
    return rows, H, N


def backtest(draws, k):
    """Walk-forward on the era with the most draws (the current era may be short)."""
    eras = {}
    for d in draws:
        eras[d["era"]] = eras.get(d["era"], 0) + 1
    if not eras:
        return None
    era = max(eras, key=eras.get)
    rows, H, N = era_matrix(draws, era)
    T = len(rows)
    if T < MIN_HISTORY + 20:
        return {"era": era, "draws": T, "models": {}}
    start = max(MIN_HISTORY, T - BACKTEST_TARGETS)
    insts = {key: make() for key, (_, _, make) in MODELS.items()}
    scores = {key: [] for key in MODELS}
    base = np.full(N, k / N)
    for t in range(start, T):
        hist, y = H[:t], H[t].astype(float)
        llb = loglik(base, y)
        for key, f in insts.items():
            p = clean(f(hist, k, N), k)
            scores[key].append((int(y[np.array(pick_top(p, k)) - 1].sum()), loglik(p, y) - llb))
    return {"era": era, "draws": T, "from": rows[start]["date"], "to": rows[-1]["date"],
            "models": {key: summarize(v, k, N) for key, v in scores.items()}}


def run_game(key):
    g = GAMES[key]
    k = g["main"]
    draws = load_draws(key)
    if not draws:
        return
    ARENA_DIR.mkdir(parents=True, exist_ok=True)
    path = ARENA_DIR / f"{key}.json"
    prev = json.loads(path.read_text()) if path.exists() else {}
    live = prev.get("live", [])
    by_date = {d["date"]: d for d in draws}
    dates = [d["date"] for d in draws]

    # 1. score live predictions whose draw has happened
    for entry in live:
        if entry.get("result"):
            continue
        later = [d for d in dates if d > entry["after"]]
        if not later:
            continue
        target = by_date[later[0]]
        if target["era"] != entry["era"]:
            entry["result"] = {"date": target["date"], "skipped": "format changed"}
            continue
        N = int(entry["era"].split("/")[1])
        y = np.zeros(N)
        y[np.array(target["main"]) - 1] = 1
        llb = loglik(np.full(N, k / N), y)
        res = {"date": target["date"], "drawn": target["main"], "models": {}}
        for mk, pred in entry["models"].items():
            p = clean(np.array(pred["probs"]), k)
            res["models"][mk] = {"matches": len(set(pred["pick"]) & set(target["main"])),
                                 "bits": round((loglik(p, y) - llb) / math.log(2), 5)}
        entry["result"] = res

    # 2. pre-register a prediction for the next draw (once per draw)
    latest = draws[-1]
    rows, H, N = era_matrix(draws, latest["era"])
    if len(rows) >= MIN_HISTORY and not any(e["after"] == latest["date"] for e in live):
        models = {}
        for mk, (_, _, make) in MODELS.items():
            p = clean(make()(H, k, N), k)
            models[mk] = {"pick": pick_top(p, k), "probs": [round(float(x), 5) for x in p]}
        live.append({"after": latest["date"], "era": latest["era"],
                     "made": datetime.now(timezone.utc).isoformat(timespec="seconds"), "models": models})

    # 3. live leaderboard
    live_rows = {mk: [] for mk in MODELS}
    for e in live:
        r = e.get("result") or {}
        for mk, s in r.get("models", {}).items():
            if mk in live_rows:
                live_rows[mk].append((s["matches"], s["bits"] * math.log(2)))
    N_cur = int(latest["era"].split("/")[1])
    out = {
        "game": key, "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "k": k, "currentEra": latest["era"], "eraDraws": len(rows), "minHistory": MIN_HISTORY,
        "models": {mk: {"name": n, "about": a} for mk, (n, a, _) in MODELS.items()},
        "liveScore": {mk: summarize(v, k, N_cur) for mk, v in live_rows.items()},
        "backtest": backtest(draws, k),
        "live": live[-300:],
    }
    path.write_text(json.dumps(out, separators=(",", ":")))
    bt = out["backtest"] or {}
    n_bt = bt.get("models", {}).get("random", {}).get("n", 0)
    print(f"{g['name']}: era {latest['era']} ({len(rows)} draws); backtest on {bt.get('era')}, {n_bt} targets")


def main():
    for key in GAMES:
        run_game(key)


if __name__ == "__main__":
    main()
