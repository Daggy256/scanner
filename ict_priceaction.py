#!/usr/bin/env python3
"""
Advanced Price Action Model  (analysis only, it does NOT place trades)

A classical price-action engine, separate from the ICT / SMC models: it reads raw candles, swings and zones only.
Works on 1m / 5m / 15m / 1h for the 4 FX/gold pairs and the NQ / ES / YM index futures.

WHAT IT READS
  Structure      swing highs / lows -> HH+HL uptrend, LH+LL downtrend, or range; EMA20 / EMA50 as dynamic value
  Volatility     ATR now vs its 50-bar average: compressing (coil) or expanding
  Levels         support / resistance ZONES: swing points clustered within 0.35 ATR, strength = number of touches.
                 A resistance that breaks becomes support (role flip) automatically.
  Zones          supply / demand: a tight base (1-3 small candles) followed by an impulsive departure (>= 2 ATR).
                 Fresh = untouched since, tested = touched once. Broken zones are dropped.

THE 5 TRADE MODELS  (each needs a candle trigger; entry is a STOP order beyond the signal candle)
  1. Zone reaction        pin bar / bullish-bearish engulfing at a fresh demand/supply zone or a strong S/R level
  2. False breakout       price breaks a level by wick or close, then reclaims it with a strong candle (spring / upthrust)
  3. Break & retest       displacement candle closes through a level, price returns to the flipped zone, holds, and rejects
  4. With-trend pullback  trend intact (EMA20 > EMA50, higher lows), pullback into EMA20 value, trigger candle
  5. Inside-bar breakout  inside bar after a >= 1 ATR mother bar, at value / support, breakout candle in trend direction

Targets = nearest opposing level or supply/demand zone giving at least 1.5R. Stop = beyond the signal candle / zone.
Score 0-5: +1 with trend, +1 strong zone/level, +1 killzone, +1 RR >= 2, +1 high-quality trigger.

Run :  python ict_priceaction.py                       (Yahoo data, 5m, all markets)
       python ict_priceaction.py --tf 5,15,60 --market index
       python ict_priceaction.py --source mt5 --mt5-offset 3 --suffix m
       python ict_priceaction.py --source demo
Needs: pip install pandas numpy yfinance   (keep ict_scanner.py in the same folder)
Educational tool. Not financial advice. Verify every setup on your own chart.
"""
import argparse
import time
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

import ict_scanner as sc

NY = sc.NY
FX = list(sc.YAHOO)
INDEX = list(sc.YAHOO_EXTRA)
ALL = FX + INDEX
MIN_RR = 1.5
FRESH_BARS = 25        # only triggers on the last 25 completed bars are reported
PEND_BARS = 12         # a stop order that has not filled 12 bars after the trigger is dropped
WINDOW = 1500          # bars analysed per timeframe


@dataclass
class PA:
    symbol: str
    tf: str
    model: str
    trigger: str
    side: str
    order: str           # "stop"
    entry: float
    sl: float
    tp: float
    rr: float
    status: str          # PENDING (order not filled) / ACTIVE (filled, running)
    formed: str
    ts: int
    score: int
    notes: str
    zlo: float = 0.0
    zhi: float = 0.0
    htf: str = ""


class X:                 # one analysed view of the data (bullish view; bearish = mirrored copy)
    pass


# ------------------------------------------------------------------ prep ----
def mirror(df):
    return pd.DataFrame({"open": -df.open, "high": -df.low, "low": -df.high, "close": -df.close}, index=df.index)


def thin(flag, vals, lowest):
    """Collapse tied swing points on neighbouring bars (a bar's open = the last close makes ties common) into ONE swing."""
    out = np.zeros(len(flag), bool)
    idx = np.flatnonzero(flag)
    group = []
    for j in list(idx) + [None]:
        if group and (j is None or j - group[-1] > 3):
            v = vals[group]
            out[group[int(np.argmin(v) if lowest else np.argmax(v))]] = True
            group = []
        if j is not None:
            group.append(j)
    return out


def prep(df, sgn, sym):
    d = df.iloc[-WINDOW:]
    if len(d) < 120:
        return None
    if sgn < 0:
        d = mirror(d)
    x = X()
    x.sgn, x.sym, x.idx, x.N = sgn, sym, d.index, len(d)
    x.o, x.h, x.l, x.c = (d[k].values for k in ("open", "high", "low", "close"))
    x.a = sc.atr(d).values
    x.e20 = d.close.ewm(span=20, adjust=False).mean().values
    x.e50 = d.close.ewm(span=50, adjust=False).mean().values
    sh, sl = sc.swings(d, 3)
    x.sh, x.sl = thin(sh, x.h, False), thin(sl, x.l, True)
    dig = sc.DIGITS.get(sym, 5)
    x.p = lambda v: f"{sgn * v:.{dig}f}"
    x.r = lambda u, v: "-".join(f"{w:.{dig}f}" for w in sorted((sgn * u, sgn * v)))     # raw price range, low-high
    return x


# ----------------------------------------------------------- levels / zones ----
def levels_asof(x, i, lookback=300, tol=0.35):
    """Support/resistance zones using only swings confirmed by bar i (a swing needs 3 bars after it)."""
    a = x.a[i]
    lo_i = max(0, i - lookback)
    pts = sorted([(x.h[j], j) for j in range(lo_i, i - 2) if x.sh[j]] + [(x.l[j], j) for j in range(lo_i, i - 2) if x.sl[j]])
    clusters, cur = [], []
    for p, j in pts:
        if cur and p - cur[-1][0] > tol * a:
            clusters.append(cur)
            cur = []
        cur.append((p, j))
    if cur:
        clusters.append(cur)
    out = []
    for cl in clusters:
        ps = [q for q, _ in cl]
        out.append(dict(lo=min(ps) - 0.1 * a, hi=max(ps) + 0.1 * a, mid=sum(ps) / len(ps), n=len(cl), last=max(j for _, j in cl)))
    return out


def find_zones(x):
    """Supply / demand: tight base then an impulsive departure. Bullish view: 'demand' below, 'supply' above."""
    zones, last = [], {"demand": -9, "supply": -9}
    for k in range(20, x.N - 4):
        a = x.a[k]
        if not np.isfinite(a):
            continue
        base = None
        for b in (3, 2, 1):                                           # widest valid base wins
            s = k - b + 1
            hh, ll = x.h[s:k + 1].max(), x.l[s:k + 1].min()
            if (x.h[s:k + 1] - x.l[s:k + 1]).max() <= 0.8 * a and hh - ll <= 1.3 * a:
                base = (s, hh, ll)
                break
        if base is None:
            continue
        s, hh, ll = base
        up, dn = x.h[k + 1:k + 4].max() - x.c[k], x.c[k] - x.l[k + 1:k + 4].min()
        top = np.maximum(x.o[s:k + 1], x.c[s:k + 1]).max()
        bot = np.minimum(x.o[s:k + 1], x.c[s:k + 1]).min()
        if up >= 2 * a and x.c[k + 1] > x.o[k + 1] and x.h[k + 1] > hh and k - last["demand"] > 3:
            zones.append(dict(kind="demand", k=k, lo=float(ll), hi=float(top)))
            last["demand"] = k
        elif dn >= 2 * a and x.c[k + 1] < x.o[k + 1] and x.l[k + 1] < ll and k - last["supply"] > 3:
            zones.append(dict(kind="supply", k=k, lo=float(bot), hi=float(hh)))
            last["supply"] = k
    return zones


def zone_state(x, z, i):
    """How many separate times price has visited the zone before bar i, and whether it was closed through."""
    s = z["k"] + 4
    if s >= i:
        return dict(touches=0, broken=False)
    seg_l, seg_h, seg_c = x.l[s:i], x.h[s:i], x.c[s:i]
    if z["kind"] == "demand":
        inside, broken = seg_l <= z["hi"], bool((seg_c < z["lo"]).any())
    else:
        inside, broken = seg_h >= z["lo"], bool((seg_c > z["hi"]).any())
    edges = int(inside[0]) + int((inside[1:] & ~inside[:-1]).sum()) if len(inside) else 0
    return dict(touches=edges, broken=broken)


# --------------------------------------------------------------- structure ----
def swing_struct(x, i):
    """Last two confirmed swing highs / lows as of bar i -> ('up'|'down'|'range', text)."""
    js_h = [j for j in range(max(0, i - 150), i - 2) if x.sh[j]]
    js_l = [j for j in range(max(0, i - 150), i - 2) if x.sl[j]]
    if len(js_h) < 2 or len(js_l) < 2:
        return "range", "not enough swings"
    hh, hl = x.h[js_h[-1]] > x.h[js_h[-2]], x.l[js_l[-1]] > x.l[js_l[-2]]
    if hh and hl:
        return "up", "higher highs + higher lows"
    if not hh and not hl:
        return "down", "lower highs + lower lows"
    return "range", "mixed swings (range / transition)"


def trend_up(x, i):
    return x.e20[i] > x.e50[i] and x.e20[i] > x.e20[i - 5]


def higher_low(x, i):
    js = [j for j in range(max(0, i - 120), i - 2) if x.sl[j]]
    return len(js) >= 2 and x.l[js[-1]] > x.l[js[-2]]


# ---------------------------------------------------------------- triggers ----
def trigger(x, i):
    """Bullish signal candle at bar i -> (name, high_quality) or None."""
    a, r = x.a[i], x.h[i] - x.l[i]
    w = "Bullish" if x.sgn > 0 else "Bearish"
    body = abs(x.c[i] - x.o[i])
    lw = min(x.o[i], x.c[i]) - x.l[i]
    if r >= 0.8 * a and lw >= 0.6 * r and body <= 0.35 * r and x.c[i] >= x.l[i] + 0.6 * r:
        return f"{w} pin bar", bool(lw >= 0.7 * r)
    if (x.c[i] > x.o[i] and x.c[i - 1] < x.o[i - 1] and x.c[i] >= x.o[i - 1] and x.o[i] <= x.c[i - 1]
            and body >= 0.6 * a):
        return f"{w} engulfing", bool(body >= 1.0 * a)
    return None


def cand(model, trg, x, i, sl_base, zone, strong, note):
    a = x.a[i]
    return dict(model=model, trigger=trg[0], q=trg[1], entry=float(x.h[i] + 0.05 * a), sl=float(sl_base - 0.1 * a),
                zone=(float(zone[0]), float(zone[1])), strong=bool(strong), note=note, i=i)


# ------------------------------------------------------------------ models ----
def m_zone(x, i, L, zs):
    trg = trigger(x, i)
    if not trg:
        return []
    a, out, p = x.a[i], [], x.p
    for z in zs:
        if z["kind"] != "demand" or z["k"] >= i - 4:
            continue
        st = zone_state(x, z, i)
        if st["broken"] or st["touches"] > 1:
            continue
        if x.l[i] <= z["hi"] and x.c[i] > z["lo"] and x.l[i] >= z["lo"] - 0.5 * a:
            out.append(cand("Zone reaction", trg, x, i, min(x.l[i], z["lo"]), (z["lo"], z["hi"]), st["touches"] == 0,
                            f"{trg[0]} reacting from a {'fresh' if st['touches'] == 0 else 'once-tested'} "
                            f"{'demand' if x.sgn > 0 else 'supply'} zone {x.r(z['lo'], z['hi'])}."))
    for lv in L:
        if lv["n"] >= 2 and x.l[i] <= lv["hi"] + 0.15 * a and x.l[i] >= lv["lo"] - 0.4 * a and x.c[i] > lv["lo"]:
            out.append(cand("Zone reaction", trg, x, i, min(x.l[i], lv["lo"]), (lv["lo"], lv["hi"]), lv["n"] >= 3,
                            f"{trg[0]} at {'support' if x.sgn > 0 else 'resistance'} {x.r(lv['lo'], lv['hi'])} ({lv['n']} touches)."))
    return out


def m_fakeout(x, i, L, zs):
    a, out, p = x.a[i], [], x.p
    if not (x.c[i] > x.o[i] and (x.c[i] - x.o[i]) >= 0.4 * a):
        return out
    for lv in L:
        if lv["n"] < 2 or x.c[i] <= lv["hi"]:          # must close back above the whole zone
            continue
        low = x.l[i - 3:i + 1].min()
        if low < lv["lo"] - 0.05 * a and low >= lv["lo"] - 1.5 * a and x.c[i] > lv["hi"]:
            trg = ("Reclaim candle" if x.sgn > 0 else "Rejection back below", bool((x.c[i] - x.o[i]) >= 1.0 * a))
            out.append(cand("False breakout", trg, x, i, low, (lv["lo"], lv["hi"]), lv["n"] >= 3,
                            f"Broke {'support' if x.sgn > 0 else 'resistance'} {x.r(lv['lo'], lv['hi'])} to {p(low)}, "
                            f"then reclaimed it ({lv['n']} touches): stops were run, the break failed."))
    return out


def m_retest(x, i, L, zs):
    a, out, p = x.a[i], [], x.p
    if not (x.c[i] > x.o[i] and x.c[i] > x.h[i - 1]):
        return out
    for lv in L:
        if lv["n"] < 2 or x.c[i] <= lv["hi"]:
            continue
        b = next((j for j in range(i - 2, max(i - 16, 1), -1)
                  if x.c[j] > lv["hi"] + 0.1 * a and (x.c[j] - x.o[j]) >= 0.7 * a and x.c[j - 1] <= lv["hi"]), None)
        if b is None or (x.c[b + 1:i + 1] < lv["lo"]).any():
            continue
        t = next((j for j in range(i, b, -1) if x.l[j] <= lv["hi"] + 0.1 * a), None)
        if t is None or t < i - 3:
            continue
        trg = ("Rejection candle" if x.sgn > 0 else "Rejection candle (down)", bool((x.c[i] - x.o[i]) >= 0.8 * a))
        out.append(cand("Break & retest", trg, x, i, min(lv["lo"], x.l[i - 3:i + 1].min()), (lv["lo"], lv["hi"]), lv["n"] >= 3,
                        f"Closed through {'resistance' if x.sgn > 0 else 'support'} {x.r(lv['lo'], lv['hi'])} at "
                        f"{x.idx[b]:%H:%M} (displacement), retested the flipped zone and held; rejection now."))
    return out


def m_pullback(x, i, L, zs):
    a, p = x.a[i], x.p
    if not (trend_up(x, i) and higher_low(x, i) and x.c[i] > x.e20[i]):
        return []
    pl = x.l[i - 5:i + 1].min()
    if not (pl <= x.e20[i] + 0.15 * a and pl >= x.e50[i] - 0.5 * a):
        return []
    if x.h[i - 20:i].max() - pl < 1.5 * a:
        return []
    trg = trigger(x, i)
    if not trg and x.c[i] > x.o[i] and x.c[i] > x.h[i - 1] and (x.c[i] - x.o[i]) >= 0.4 * a:
        trg = (f"{'Bullish close above prior high' if x.sgn > 0 else 'Bearish close below prior low'}", False)
    if not trg:
        return []
    zl, zh = min(x.e20[i], x.e50[i]), max(x.e20[i], x.e50[i])
    return [cand("With-trend pullback", trg, x, i, pl, (zl, zh), True,
                 f"{'Uptrend' if x.sgn > 0 else 'Downtrend'} intact (EMA20 {'>' if x.sgn > 0 else '<'} EMA50, {'higher lows' if x.sgn > 0 else 'lower highs'}); "
                 f"pullback to EMA20 {p(x.e20[i])} held at {p(pl)}; {trg[0]}.")]


def m_insidebar(x, i, L, zs):
    a, p = x.a[i], x.p
    m, ib = i - 2, i - 1
    if not (x.h[ib] < x.h[m] and x.l[ib] > x.l[m] and (x.h[m] - x.l[m]) >= 1.0 * a):
        return []
    if not (x.c[i] > x.h[m] and (x.c[i] - x.o[i]) >= 0.4 * a and trend_up(x, i)):
        return []
    at_value = x.l[m] <= x.e20[m] + 0.5 * a or any(lv["n"] >= 2 and abs(x.l[m] - lv["hi"]) <= 0.5 * a for lv in L)
    if not at_value:
        return []
    trg = ("Inside-bar breakout", bool((x.c[i] - x.o[i]) >= 1.0 * a))
    return [cand("Inside-bar breakout", trg, x, i, x.l[m], (x.l[m], x.h[m]), True,
                 f"Inside bar after a {(x.h[m] - x.l[m]) / a:.1f} ATR mother bar {x.r(x.l[m], x.h[m])} at value, "
                 f"broke {'up' if x.sgn > 0 else 'down'} with the trend.")]


MODELS = (m_zone, m_fakeout, m_retest, m_pullback, m_insidebar)


# --------------------------------------------------------------- finalize ----
def order_state(x, i, entry, sl, tp):
    """PENDING (stop not filled yet), ACTIVE (filled, running) or None (dead: stopped, finished, invalidated, expired)."""
    filled = False
    for k in range(i + 1, x.N):
        if not filled:
            if x.l[k] <= sl:
                return None
            if k - i > PEND_BARS:
                return None
            if x.h[k] >= entry:
                filled = True
                if x.l[k] <= sl or x.h[k] >= tp:
                    return None
        elif x.l[k] <= sl or x.h[k] >= tp:
            return None
    return "ACTIVE" if filled else "PENDING"


def finalize(x, tf, c, L, zs, i):
    a, entry, sl = x.a[i], c["entry"], c["sl"]
    risk = entry - sl
    if risk < 0.3 * a or risk > 4 * a:
        return None
    above = [lv["lo"] for lv in L if lv["lo"] > entry]
    for z in zs:
        if z["kind"] == "supply" and z["k"] < i - 4 and z["lo"] > entry and not zone_state(x, z, i)["broken"]:
            above.append(z["lo"])
    tp = next((t - 0.05 * a for t in sorted(above) if (t - 0.05 * a - entry) / risk >= MIN_RR), None)
    if tp is None:
        return None
    st = order_state(x, i, entry, sl, tp)
    if st is None or (st == "PENDING" and abs(x.c[-1] - entry) > 3 * a):
        return None
    if st == "ACTIVE" and (x.c[-1] - entry) > 1.0 * risk:      # already 1R in profit: too late to take
        return None
    rr = (tp - entry) / risk
    t = x.idx[i]
    score = int(trend_up(x, i)) + int(c["strong"]) + int(t.hour in sc.KZ_HOURS) + int(rr >= 2) + int(c["q"])
    sgn = x.sgn
    z = sorted(sgn * v for v in c["zone"])
    return PA(x.sym, tf, c["model"], c["trigger"], "buy" if sgn > 0 else "sell", "stop",
              sgn * entry, sgn * sl, sgn * tp, round(rr, 2), st, t.strftime("%H:%M"), int(t.timestamp()), score,
              c["note"] + f" Stop order {x.p(entry)}, invalidated beyond {x.p(sl)}.", float(z[0]), float(z[1]))


def scan_view(x, tf):
    zs = find_zones(x)
    res = []
    for i in range(max(80, x.N - FRESH_BARS), x.N):
        if not np.isfinite(x.a[i]):
            continue
        L = levels_asof(x, i)
        for m in MODELS:
            for c in m(x, i, L, zs):
                s = finalize(x, tf, c, L, zs, i)
                if s:
                    res.append(s)
    return res


def market_read(x, tf):
    """Plain-language read of the chart now (raw view)."""
    i, p, a = x.N - 1, x.p, x.a[x.N - 1]
    kind, txt = swing_struct(x, i)
    price = x.c[i]
    ema = ("above" if price > x.e20[i] > x.e50[i] else "below" if price < x.e20[i] < x.e50[i] else "between") + " EMA20/50"
    base = x.a[max(0, i - 50):i + 1]
    ratio = float(a / np.nanmean(base)) if np.isfinite(a) else 1.0
    vol = "expanding" if ratio > 1.25 else "compressing (coil, expect expansion)" if ratio < 0.8 else "normal"
    L = levels_asof(x, i)
    sup = sorted([lv for lv in L if lv["hi"] < price], key=lambda v: -v["hi"])[:2]
    res = sorted([lv for lv in L if lv["lo"] > price], key=lambda v: v["lo"])[:2]
    at = [lv for lv in L if lv["lo"] <= price <= lv["hi"] and lv["n"] >= 2]
    zs = [z for z in find_zones(x) if not zone_state(x, z, i)["broken"] and z["k"] < i - 4]
    dem = [z for z in zs if z["kind"] == "demand" and z["hi"] < price and zone_state(x, z, i)["touches"] <= 1]
    sup_z = [z for z in zs if z["kind"] == "supply" and z["lo"] > price and zone_state(x, z, i)["touches"] <= 1]
    dem = sorted(dem, key=lambda z: -z["hi"])[:1]
    sup_z = sorted(sup_z, key=lambda z: z["lo"])[:1]
    fz = lambda z: f"{x.r(z['lo'], z['hi'])}"
    return dict(symbol=x.sym, tf=tf, price=float(price), trend={"up": "UPTREND", "down": "DOWNTREND", "range": "RANGE"}[kind],
                structure=txt, ema=ema, volatility=vol, atr=float(a),
                support=[dict(lo=float(lv["lo"]), hi=float(lv["hi"]), n=lv["n"]) for lv in sup],
                resistance=[dict(lo=float(lv["lo"]), hi=float(lv["hi"]), n=lv["n"]) for lv in res],
                at_level=[dict(lo=float(lv["lo"]), hi=float(lv["hi"]), n=lv["n"]) for lv in at],
                demand=[dict(lo=z["lo"], hi=z["hi"]) for z in dem], supply=[dict(lo=z["lo"], hi=z["hi"]) for z in sup_z],
                text=(f"{tf} {kind.upper() if kind != 'range' else 'RANGE'}: {txt}; price {ema}; volatility {vol}. "
                      + ("AT LEVEL " + ", ".join(f"{fz(lv)} ({lv['n']}x)" for lv in at) + ". " if at else "")
                      + ("Support " + ", ".join(f"{fz(lv)} ({lv['n']}x)" for lv in sup) + ". " if sup else "")
                      + ("Resistance " + ", ".join(f"{fz(lv)} ({lv['n']}x)" for lv in res) + ". " if res else "")
                      + ("Fresh demand " + fz(dem[0]) + ". " if dem else "") + ("Fresh supply " + fz(sup_z[0]) + "." if sup_z else "")).strip())


def analyze(sym, df, tf):
    """df = completed bars of one timeframe. Returns (setups, read)."""
    found, read = [], None
    for sgn in (1, -1):
        x = prep(df, sgn, sym)
        if x is None:
            continue
        found += scan_view(x, tf)
        if sgn == 1:
            read = market_read(x, tf)
    best = {}
    for s in found:                                          # best per model / side
        k = (s.model, s.side)
        if k not in best or (s.score, s.ts) > (best[k].score, best[k].ts):
            best[k] = s
    return list(best.values()), read


def completed(raw, m):
    d = sc.to_tf(raw, m)
    return d.iloc[:-1] if sc.BASE[m] == m else d             # base-timeframe data still contains the forming bar


def run(args, symbols=None, tfs=(5,), bias_fn=None):
    symbols = symbols or FX
    setups, reads, errors = [], [], []
    for sym in symbols:
        raw = {}
        for m in tfs:
            try:
                b = sc.BASE[m]
                if b not in raw:
                    raw[b] = sc.LOADERS[args.source](sym, args, b)
                s, r = analyze(sym, completed(raw[b], m), sc.TF_LABEL[m])
                setups += s
                if r:
                    reads.append(r)
            except Exception as e:
                errors.append(f"{sym} {sc.TF_LABEL[m]}: {e}")
        b = bias_fn(sym) if bias_fn and sym in FX else None
        for s in setups:
            if s.symbol == sym:
                s.htf = "" if not b else "neutral" if b["dir"] == 0 else "aligned" if (b["dir"] > 0) == (s.side == "buy") else "counter"
    setups.sort(key=lambda s: (s.htf != "aligned", -s.score, -s.rr))
    return dict(setups=[asdict(s) for s in setups], reads=reads, errors=errors, scanned=time.strftime("%H:%M:%S"),
                digits={s: sc.DIGITS.get(s, 5) for s in symbols})


# ------------------------------------------------------------------ output ----
def show(s, dig):
    f = lambda v: f"{v:.{dig}f}"
    print(f"[{s['score']}/5] {s['symbol']} {s['tf']} | {s['model']} ({s['trigger']}) | {s['side'].upper()} STOP {f(s['entry'])}  "
          f"SL {f(s['sl'])}  TP {f(s['tp'])}  RR {s['rr']}  [{s['status']}]  trigger {s['formed']} NY"
          + (f"  <{s['htf']} HTF>" if s["htf"] else ""))
    print(f"       {s['notes']}")


def main():
    ap = argparse.ArgumentParser(description="Advanced price action model")
    ap.add_argument("--source", choices=sc.LOADERS, default="yahoo")
    ap.add_argument("--market", choices=["fx", "index", "all"], default="all")
    ap.add_argument("--symbols", nargs="+", default=None, help=f"any of: {' '.join(ALL)}")
    ap.add_argument("--tf", default="5", help="1 | 5 | 15 | 60 | both | all | comma list e.g. 5,15,60")
    ap.add_argument("--min-score", type=int, default=2)
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--no-read", action="store_true", help="hide the market read")
    ap.add_argument("--htf", action="store_true", help="tag FX setups aligned / counter to the HTF bias (needs ict_htf_bias.py)")
    ap.add_argument("--watch", type=int, default=0, help="re-scan every N seconds, print only NEW setups")
    ap.add_argument("--suffix", default="")
    ap.add_argument("--mt5-offset", type=float, default=0)
    ap.add_argument("--mt5-map", default="", help="broker names, e.g. NQ=USTEC,ES=US500,YM=US30")
    ap.add_argument("--seed", default=0)
    args = ap.parse_args()
    for kv in filter(None, args.mt5_map.split(",")):
        k, v = kv.split("=")
        sc.MT5_NAMES[k.strip().upper()] = v.strip()
    symbols = args.symbols or {"fx": FX, "index": INDEX}.get(args.market, ALL)
    bias_fn = None
    if args.htf:
        import ict_htf_bias as hb
        bias_fn = lambda s: hb.compute(s, args)
    seen = set()
    while True:
        r = run(args, symbols, sc.parse_tfs(args.tf), bias_fn)
        print(f"\n===== PRICE ACTION scan {pd.Timestamp.now(tz=NY):%a %Y-%m-%d %H:%M} NY =====")
        for e in r["errors"]:
            print("!", e)
        if not args.no_read:
            for rd in r["reads"]:
                print(f"  {rd['symbol']} {rd['text']}")
        new = [s for s in r["setups"] if s["score"] >= args.min_score and (s["symbol"], s["tf"], s["model"], s["side"], s["ts"]) not in seen][:args.top]
        print("No new setups." if not new else f"{len(new)} setup(s):")
        for s in new:
            show(s, r["digits"][s["symbol"]])
            seen.add((s["symbol"], s["tf"], s["model"], s["side"], s["ts"]))
        if not args.watch:
            break
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
