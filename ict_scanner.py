#!/usr/bin/env python3
"""
ICT Setup Scanner  (analysis / alerts only, it does NOT place trades)

Pairs : XAUUSD, USDJPY, EURUSD, GBPUSD
Strategy modes (--strategy ict | smc | both):
  ICT models: 1) Silver Bullet   2) Judas Swing   3) Liquidity Sweep + MSS + FVG
        4) Order Block (after sweep + MSS)  5) OTE (62-79% retracement)
  SMC models: 6) BOS + OB (trend continuation)  7) CHoCH + OB (trend reversal)

All times are New York time. Timeframes: 1m, 5m, 15m, 1h  (--tf 1 | 5 | 15 | 60 | both | all, or a list like 1,5,60).
  'both' = 5m+15m (as before), 'all' = 1m+5m+15m+1h. Silver Bullet is skipped on 1h (its window is one bar).

Install : pip install pandas numpy yfinance
Run     : python ict_scanner.py                  (Yahoo data, one scan)
          python ict_scanner.py --watch 300      (re-scan every 5 min, print only NEW setups)
          python ict_scanner.py --tf 1,60 --watch 60   (1-minute and 1-hour setups)
          python ict_scanner.py --source mt5 --mt5-offset 3 --suffix m
          python ict_scanner.py --source demo    (synthetic data, to test the pipeline)

Educational tool. Not financial advice. Verify every setup on your chart.
"""
import argparse
import time
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

NY = "America/New_York"
YAHOO = {"XAUUSD": "GC=F", "USDJPY": "USDJPY=X", "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X"}
DIGITS = {"XAUUSD": 2, "USDJPY": 3, "EURUSD": 5, "GBPUSD": 5, "NQ": 2, "ES": 2, "YM": 0}
# US index futures (used by ict_smt.py only; not part of the FX scan list above)
YAHOO_EXTRA = {"NQ": "NQ=F", "ES": "ES=F", "YM": "YM=F"}
MT5_NAMES = {}      # broker symbol overrides, e.g. {"NQ": "USTEC", "ES": "US500", "YM": "US30"} (set by ict_smt.py --mt5-map)
DEMO_BASE = {"XAUUSD": 2650.0, "USDJPY": 150.0, "EURUSD": 1.08, "GBPUSD": 1.27, "NQ": 20500.0, "ES": 5800.0, "YM": 42000.0}
KZ_HOURS = {2, 3, 4, 7, 8, 9, 10, 13, 14, 15}   # London / NY AM / NY PM killzones (NY time)
SB_HOURS = {3, 10, 14}                          # Silver Bullet windows: 03-04, 10-11, 14-15
MIN_RR = 1.5
BASE = {1: 1, 5: 5, 15: 5, 60: 60}          # chart timeframe -> native data timeframe it is built from
TF_LABEL = {1: "1m", 5: "5m", 15: "15m", 60: "1h"}


def parse_tfs(s):
    """'5' -> [5]; 'both' -> [5, 15]; 'all' -> [1, 5, 15, 60]; '1,60' or '1h' -> [1, 60]."""
    s = str(s).strip().lower()
    if s == "both":
        return [5, 15]
    if s == "all":
        return [1, 5, 15, 60]
    out = []
    for tok in s.split(","):
        tok = tok.strip()
        m = 60 if tok in ("1h", "h1", "60", "60m") else int(tok.rstrip("m"))
        if m not in BASE:
            raise ValueError(f"unsupported timeframe {tok!r} (use 1, 5, 15, 60)")
        out.append(m)
    return out


# ----------------------------------------------------------------- data ----
def norm(df):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [str(c).lower() for c in df.columns]
    df = df[["open", "high", "low", "close"]].dropna().astype(float)
    idx = df.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    df.index = idx.tz_convert(NY)
    return df[~df.index.duplicated()].sort_index()


def load_yahoo(sym, args, base=5):
    import yfinance as yf
    period, interval = {1: ("5d", "1m"), 5: ("7d", "5m"), 60: ("60d", "1h")}[base]
    df = yf.download({**YAHOO, **YAHOO_EXTRA}[sym], period=period, interval=interval, progress=False, auto_adjust=False)
    return norm(df)


def load_mt5(sym, args, base=5):
    import MetaTrader5 as mt5
    if not mt5.initialize():
        raise RuntimeError("MT5 terminal not running / not logged in")
    name = MT5_NAMES.get(sym, sym) + args.suffix
    mt5.symbol_select(name, True)
    tf = {1: mt5.TIMEFRAME_M1, 5: mt5.TIMEFRAME_M5, 60: mt5.TIMEFRAME_H1}[base]
    count = {1: 5000, 5: 2500, 60: 1500}[base]
    rates = mt5.copy_rates_from_pos(name, tf, 0, count)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"no MT5 data for {name}")
    df = pd.DataFrame(rates)
    df.index = pd.to_datetime(df["time"], unit="s") - pd.Timedelta(hours=args.mt5_offset)
    return norm(df)


def load_demo(sym, args, base=5):
    rng = np.random.default_rng(sum(map(ord, sym)) + int(args.seed) + base)
    px = DEMO_BASE[sym]
    n = {1: 5 * 1440, 5: 6 * 288, 60: 60 * 24}[base]
    idx = pd.date_range(end=pd.Timestamp.now(tz=NY).floor(f"{base}min"), periods=n, freq=f"{base}min")
    vol = px * 0.00035 * (base / 5) ** 0.5
    shock = 1 + 4 * (rng.random(n) < 0.04)
    close = px + np.cumsum(rng.normal(0, vol, n) * shock)
    open_ = np.r_[px, close[:-1]]
    high = np.maximum(open_, close) + np.abs(rng.normal(0, vol * 0.6, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, vol * 0.6, n))
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)


LOADERS = {"yahoo": load_yahoo, "mt5": load_mt5, "demo": load_demo}


def fetch(sym, args, m):
    """Load the native data needed for chart timeframe m (1m -> 1m bars, 5m/15m -> 5m bars, 1h -> 1h bars)."""
    return LOADERS[args.source](sym, args, BASE[m])


def to_tf(df, minutes, keep_partial=False):
    """Build the chart timeframe from its native data. The still-forming last bar is dropped (unless keep_partial)."""
    base = BASE[minutes]
    if minutes == base:
        return df
    r = df.resample(f"{minutes}min")
    out = r.agg({"open": "first", "high": "max", "low": "min", "close": "last"})
    cnt = r.close.count()
    out = out[cnt >= 1].dropna()
    if not keep_partial and len(out) and cnt[out.index[-1]] < minutes // base:
        out = out.iloc[:-1]
    return out


# ------------------------------------------------------------ primitives ----
def atr(d, n=14):
    pc = d.close.shift()
    tr = pd.concat([d.high - d.low, (d.high - pc).abs(), (d.low - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def swings(d, n=3):
    w = 2 * n + 1
    sh = d.high.eq(d.high.rolling(w, center=True).max()).values
    sl = d.low.eq(d.low.rolling(w, center=True).min()).values
    return sh, sl


@dataclass
class Setup:
    symbol: str
    model: str
    side: str
    entry: float
    sl: float
    tp: float
    rr: float
    status: str
    formed: str
    score: int
    notes: str
    htf: str = ""
    tf: str = "5m"
    ts: int = 0          # epoch of the bar the setup formed on (for charting)
    zlo: float = 0.0     # entry zone (FVG / OB body / OTE band), low
    zhi: float = 0.0     # entry zone, high
    strat: str = "ICT"   # ICT or SMC


# -------------------------------------------------------------- context ----
def context(df, sym, flip):
    """
    Everything below is written for ONE direction: 'price swept highs, then sells'.
    Buys are found by running the same logic on a mirrored (negated) copy of the data
    and un-mirroring the prices at the end. sgn = +1 (sell view) or -1 (buy view).
    """
    sgn = -1 if flip else 1
    d = (pd.DataFrame({"open": -df.open, "high": -df.low, "low": -df.high, "close": -df.close},
                      index=df.index) if flip else df.copy())
    days = d.index.normalize().unique()
    if len(days) < 2:
        return None
    today, prev = days[-1], days[-2]
    ti = int(d.index.searchsorted(today))
    if ti >= len(d) - 3:
        return None
    pdd = d[d.index.normalize() == prev]
    asia = d[(d.index >= today - pd.Timedelta(hours=4)) & (d.index < today)]
    if pdd.empty or asia.empty:
        return None
    a = atr(d).iloc[-1]
    if not np.isfinite(a) or a <= 0:
        return None

    h, l, c, o = (d[k].values for k in ("high", "low", "close", "open"))
    sh, sl = swings(d)
    dig = DIGITS[sym]
    p = lambda x: f"{sgn * x:.{dig}f}"

    levels = {"PD": (pdd.high.max(), ti), "Asia": (asia.high.max(), ti)}      # highs to be swept
    lows = [pdd.low.min(), asia.low.min()]                                     # sell-side targets
    lon = d[(d.index >= today + pd.Timedelta(hours=2)) & (d.index < today + pd.Timedelta(hours=5))]
    i5 = int(d.index.searchsorted(today + pd.Timedelta(hours=5)))
    if not lon.empty and i5 < len(d):
        levels["London"] = (lon.high.max(), i5)
        lows.append(lon.low.min())
    lo_today = l[ti:].min()
    sw_idx = [i for i in range(max(0, len(d) - 400), len(d)) if sl[i]]
    lv_lows = list(lows)
    lows += [l[i] for i in sw_idx]
    tgts = sorted({x for x in lows if x < lo_today}, reverse=True)
    # SMC targets: swing lows nobody has broken yet + untaken session / previous-day lows
    unbroken = [l[i] for i in sw_idx if i + 1 < len(l) and l[i + 1:].min() > l[i]]
    tgts_s = sorted({*unbroken, *[x for x in lv_lows if x < lo_today]}, reverse=True)

    return dict(sym=sym, sgn=sgn, d=d, h=h, l=l, c=c, o=o, sh=sh, sl=sl, a=a, p=p, ti=ti, i5=i5,
                i8=int(d.index.searchsorted(today + pd.Timedelta(hours=8))),
                M=o[ti], levels=levels, tgts=tgts, tgts_s=tgts_s, pdmid=(pdd.high.max() + pdd.low.min()) / 2)


def status(ctx, i0, entry, sl, tp):
    h, l = ctx["h"], ctx["l"]
    filled = False
    for k in range(i0 + 1, len(h)):
        if not filled:
            if h[k] >= entry:
                filled = True
            elif l[k] <= tp:
                return None                      # target hit before we could enter
        if filled and (h[k] >= sl or l[k] <= tp):
            return None                          # stopped out or already completed
    return "FILLED" if filled else "PENDING"


def build(ctx, model, i0, entry, stop, notes, strong, zone=None, tgts=None):
    a = ctx["a"]
    sl = stop + 0.1 * a
    risk = sl - entry
    if risk <= 0 or risk > 5 * a:
        return None
    tp = next((x for x in (ctx["tgts"] if tgts is None else tgts) if x < entry and (entry - x) / risk >= MIN_RR), None)
    if tp is None:
        return None
    st = status(ctx, i0, entry, sl, tp)
    if st is None:
        return None
    if st == "PENDING" and abs(ctx["c"][-1] - entry) > 8 * a:
        return None
    rr = (entry - tp) / risk
    t = ctx["d"].index[i0]
    score = int(t.hour in KZ_HOURS) + int(rr >= 2) + int(entry > ctx["pdmid"]) + int(strong)
    sgn = ctx["sgn"]
    z = sorted(sgn * x for x in zone) if zone else [0.0, 0.0]
    return Setup(ctx["sym"], model, "sell" if sgn == 1 else "buy",
                 sgn * entry, sgn * sl, sgn * tp, round(rr, 2), st, t.strftime("%H:%M"), score, notes,
                 ts=int(t.timestamp()), zlo=float(z[0]), zhi=float(z[1]))


# --------------------------------------------------------------- models ----
def find_leg(ctx, k):
    """Sweep at bar k, then market-structure shift (close below prior swing low) with displacement."""
    h, l, c, a = ctx["h"], ctx["l"], ctx["c"], ctx["a"]
    prior = [j for j in range(max(0, k - 48), k - 3) if ctx["sl"][j]]
    if not prior:
        return None
    ref = l[prior[-1]]
    for j in range(k + 1, min(k + 36, len(c))):
        if c[j] < ref:
            ext, end = h[k:j + 1].max(), l[k:j + 1].min()
            if (h[k:j + 1] - l[k:j + 1]).max() < 1.2 * a or ext - end < 1.5 * a:
                return None
            return dict(k=k, j=j, ext=ext, end=end, ref=ref)
    return None


def find_fvg(ctx, k, j):
    h, l, c = ctx["h"], ctx["l"], ctx["c"]
    for i in range(min(j + 1, len(h) - 1), k, -1):
        if (i >= 2 and h[i] < l[i - 2] and (l[i - 2] - h[i]) >= 0.15 * ctx["a"]
                and not (c[i + 1:] > l[i - 2]).any()):
            return h[i], l[i - 2], i
    return None


def leg_setups(ctx, name, lg):
    h, l, c, o, a, p, d = ctx["h"], ctx["l"], ctx["c"], ctx["o"], ctx["a"], ctx["p"], ctx["d"]
    k, j, ext, end = lg["k"], lg["j"], lg["ext"], lg["end"]
    word = "high" if ctx["sgn"] == 1 else "low"
    base = (f"Swept {name} {word} ({p(ctx['levels'][name][0])}); MSS at {d.index[j]:%H:%M} "
            f"(close beyond {p(lg['ref'])}).")
    strong = (ext - end) >= 2.5 * a
    out = []

    fv = find_fvg(ctx, k, j)
    if fv:
        out.append(build(ctx, "Sweep+MSS+FVG", fv[2], (fv[0] + fv[1]) / 2, ext,
                         f"{base} Limit at FVG midpoint ({p(fv[0])}-{p(fv[1])}).", strong, (fv[0], fv[1])))

    bodies = o[k:j + 1] - c[k:j + 1]
    dj = k + int(np.argmax(bodies))
    ob = next((i for i in range(dj - 1, k - 4, -1) if i >= 0 and c[i] > o[i]), None)
    if ob is not None and end < o[ob] < ext:
        out.append(build(ctx, "Order Block", j, o[ob], ext,
                         f"{base} Limit at OB open {p(o[ob])} (candle {d.index[ob]:%H:%M}).", strong, (o[ob], c[ob])))

    ote = end + 0.705 * (ext - end)
    out.append(build(ctx, "OTE 62-79%", j, ote, ext,
                     f"{base} Limit at 70.5% retrace {p(ote)} (zone {p(end + 0.62 * (ext - end))}"
                     f"-{p(end + 0.79 * (ext - end))}).", strong,
                     (end + 0.62 * (ext - end), end + 0.79 * (ext - end))))
    return [s for s in out if s]


def judas(ctx):
    if "Asia" not in ctx["levels"]:
        return []
    ash = ctx["levels"]["Asia"][0]
    h, p, d = ctx["h"], ctx["p"], ctx["d"]
    k = next((i for i in range(ctx["ti"], min(ctx["i5"], len(h))) if h[i] > ash), None)
    if k is None:
        return []
    lg = find_leg(ctx, k)
    if not lg or lg["ext"] <= ctx["M"] or lg["j"] > ctx["i8"]:
        return []
    fv = find_fvg(ctx, lg["k"], lg["j"])
    entry = (fv[0] + fv[1]) / 2 if fv else (lg["ext"] + lg["end"]) / 2
    i0 = fv[2] if fv else lg["j"]
    word = "up" if ctx["sgn"] == 1 else "down"
    s = build(ctx, "Judas Swing", i0, entry, lg["ext"],
              f"False move {word} through midnight open ({p(ctx['M'])}) and Asia range extreme "
              f"({p(ash)}), then MSS {d.index[lg['j']]:%H:%M}. Entry at "
              f"{'FVG midpoint' if fv else '50% of leg'}.", True, (fv[0], fv[1]) if fv else None)
    return [s] if s else []


def silver_bullet(ctx):
    h, l, c, a, p, d = ctx["h"], ctx["l"], ctx["c"], ctx["a"], ctx["p"], ctx["d"]
    best = {}
    for i in range(max(ctx["ti"], 14), len(h)):
        t = d.index[i]
        if t.hour not in SB_HOURS or not (h[i] < l[i - 2]) or (h[i - 1] - l[i - 1]) < a:
            continue
        if (l[i - 2] - h[i]) < 0.15 * a:
            continue
        if (c[i + 1:] > l[i - 2]).any():
            continue
        lo = max(0, i - 60)
        ref = h[lo:i - 12].max() if i - 12 > lo else None
        ext = h[i - 12:i + 1].max()
        if ref is None or ext <= ref:
            continue
        entry = (h[i] + l[i - 2]) / 2
        s = build(ctx, "Silver Bullet", i, entry, ext,
                  f"{t:%H:%M} FVG ({p(h[i])}-{p(l[i - 2])}) after liquidity sweep ({p(ref)}) inside "
                  f"the {t.hour:02d}:00 window. Limit at FVG midpoint.", (h[i - 1] - l[i - 1]) >= 1.5 * a,
                  (h[i], l[i - 2]))
        if s:
            best[(t.date(), t.hour)] = s
    return list(best.values())


# ------------------------------------------------------ smart money concepts ----
def _smc_ev(d, h, l, c, o, a, i, kind, dr, ref):
    """Describe one structure break: the order block behind it and how strong the impulse was."""
    lvl, li = ref
    ai = a[i]
    if not np.isfinite(ai):
        return None
    if dr == "bull":                                    # impulse up from the lowest low since the broken high
        m = li + int(np.argmin(l[li:i + 1]))
        imp = h[m:i + 1].max() - l[m]
        k = next((x for x in range(m, max(li - 1, m - 6), -1) if c[x] < o[x]), None)    # last DOWN candle
    else:
        m = li + int(np.argmax(h[li:i + 1]))
        imp = h[m] - l[m:i + 1].min()
        k = next((x for x in range(m, max(li - 1, m - 6), -1) if c[x] > o[x]), None)    # last UP candle
    if k is None or imp < 1.5 * ai or (h[m:i + 1] - l[m:i + 1]).max() < 1.2 * ai:
        return None
    zlo, zhi = float(l[k]), float(h[k])
    if dr == "bull":
        broken, touched = bool((c[i + 1:] < zlo).any()), bool((l[i + 1:] <= zhi).any())
    else:
        broken, touched = bool((c[i + 1:] > zhi).any()), bool((h[i + 1:] >= zlo).any())
    return dict(i=i, kind=kind, dir=dr, level=float(lvl), li=int(li), strong=bool(imp >= 2.5 * ai),
                ob=dict(k=int(k), lo=zlo, hi=zhi, broken=broken, touched=touched))


def smc_events(d, n=3):
    """
    Market-structure tracker (works on any OHLC frame, both directions at once).
      BOS   = close beyond the latest swing point IN the direction of the current trend (continuation)
      CHoCH = close beyond the latest swing point AGAINST the current trend (first sign of reversal)
    Each event carries the order block (last opposing candle before the impulse that caused the break).
    Swings are confirmed n bars late, so there is no look-ahead.
    """
    h, l, c, o = (d[k].values for k in ("high", "low", "close", "open"))
    a = atr(d).values
    sh, sl = swings(d, n)
    evs, trend, hi, lo = [], 0, None, None
    for i in range(len(d)):
        j = i - n
        if j >= 0:
            if sh[j]:
                hi = (h[j], j)
            if sl[j]:
                lo = (l[j], j)
        if hi and c[i] > hi[0] and i > hi[1]:
            if trend != 0:
                evs.append(_smc_ev(d, h, l, c, o, a, i, "CHoCH" if trend == -1 else "BOS", "bull", hi))
            trend, hi = 1, None
        elif lo and c[i] < lo[0] and i > lo[1]:
            if trend != 0:
                evs.append(_smc_ev(d, h, l, c, o, a, i, "CHoCH" if trend == 1 else "BOS", "bear", lo))
            trend, lo = -1, None
    return [e for e in evs if e]


def smc_view_setups(ctx):
    """Bearish-view SMC setups: retrace into the order block that caused a BOS / CHoCH."""
    d, p, n = ctx["d"], ctx["p"], len(ctx["d"])
    word = "below" if ctx["sgn"] == 1 else "above"
    out = []
    for e in smc_events(d):
        ob = e["ob"]
        if e["dir"] != "bear" or e["i"] < n - 150 or ob["broken"]:
            continue
        entry = (ob["lo"] + ob["hi"]) / 2                              # 50% mean threshold of the OB candle
        why = ("Structure flipped: first close against the prior trend." if e["kind"] == "CHoCH"
               else "Trend continuation: close beyond the latest swing in trend direction.")
        note = (f"{e['kind']} at {d.index[e['i']]:%H:%M}: closed {word} {p(e['level'])}. {why} "
                f"OB candle {d.index[ob['k']]:%H:%M} zone {' - '.join(sorted([p(ob['lo']), p(ob['hi'])], key=float))}; limit at 50% ({p(entry)}), "
                f"stop beyond the OB.")
        s = build(ctx, f"SMC {e['kind']} + OB", e["i"], entry, ob["hi"], note, e["strong"],
                  (ob["lo"], ob["hi"]), ctx["tgts_s"])
        if s:
            out.append(s)
    return out


def scan(sym, df, tf="5m", strategy="ict"):
    res = []
    for flip in (False, True):
        ctx = context(df, sym, flip)
        if not ctx:
            continue
        h = ctx["h"]
        if strategy in ("ict", "both"):
            for name, (lvl, st) in ctx["levels"].items():
                k = next((i for i in range(st, len(h)) if h[i] > lvl), None)
                if k is None:
                    continue
                lg = find_leg(ctx, k)
                if lg:
                    res += leg_setups(ctx, name, lg)
            res += judas(ctx) + (silver_bullet(ctx) if tf != "1h" else [])
        if strategy in ("smc", "both"):
            res += smc_view_setups(ctx)
    best = {}
    for s in res:                                   # keep best per symbol/model/side
        key = (s.symbol, s.model, s.side)
        if key not in best or (s.score, s.formed) > (best[key].score, best[key].formed):
            best[key] = s
    for s in best.values():
        s.tf = tf
        s.strat = "SMC" if s.model.startswith("SMC") else "ICT"
    return list(best.values())


# --------------------------------------------------------------- output ----
def show(s, last):
    dig = DIGITS[s.symbol]
    f = lambda x: f"{x:.{dig}f}"
    print(f"[{s.score}/4] {s.symbol} {s.tf} | {s.model} | {s.side.upper()} LIMIT {f(s.entry)}  "
          f"SL {f(s.sl)}  TP {f(s.tp)}  RR {s.rr}  [{s.status}]  formed {s.formed} NY"
          + (f"  <{s.htf}>" if s.htf else ""))
    print(f"       {s.notes}")


def run_once(args, seen):
    found, last = [], {}
    tfs = parse_tfs(args.tf)
    for sym in args.symbols:
        try:
            raw = {}
            for m in tfs:
                b = BASE[m]
                if b not in raw:
                    raw[b] = LOADERS[args.source](sym, args, b)
                last[sym] = raw[b].close.iloc[-1]
                found += scan(sym, to_tf(raw[b], m), TF_LABEL[m], args.strategy)
        except Exception as e:
            print(f"! {sym}: {e}")
    bias = {}
    if args.htf:
        import ict_htf_bias as hb
        for sym in args.symbols:
            try:
                bias[sym] = hb.compute(sym, args)
            except Exception as e:
                print(f"! HTF bias {sym}: {e}")
        for s in found:
            b = bias.get(s.symbol)
            if b:
                ok = (b["dir"] > 0 and s.side == "buy") or (b["dir"] < 0 and s.side == "sell")
                s.htf = ("HTF neutral" if b["dir"] == 0 else
                         f"ALIGNED with {b['label']} HTF" if ok else f"COUNTER-TREND vs {b['label']} HTF")
    found.sort(key=lambda s: (not s.htf.startswith("ALIGNED"), -s.score, -s.rr))
    found = found[:args.top]
    new = [s for s in found if (s.symbol, s.tf, s.model, s.side, round(s.entry, 2)) not in seen]
    now = pd.Timestamp.now(tz=NY)
    print(f"\n===== {args.strategy.upper()} scan {now:%a %Y-%m-%d %H:%M} NY | " +
          " ".join(f"{k} {v:.{DIGITS[k]}f}" for k, v in last.items()) + " =====")
    if bias:
        print("HTF bias: " + " | ".join(f"{k} {b['label']} ({b['score']:+d})" for k, b in bias.items()))
    if not new:
        print("No new qualifying setups.")
    for s in new:
        show(s, last)
        seen.add((s.symbol, s.tf, s.model, s.side, round(s.entry, 2)))
    if new and args.csv:
        out = pd.DataFrame([dict(asdict(s), scanned=str(now)) for s in new])
        out.to_csv(args.csv, mode="a", header=not pd.io.common.file_exists(args.csv), index=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=LOADERS, default="yahoo")
    ap.add_argument("--symbols", nargs="+", default=list(YAHOO))
    ap.add_argument("--strategy", choices=["ict", "smc", "both"], default="ict", help="ICT models, SMC (BOS/CHoCH/OB) models, or both")
    ap.add_argument("--tf", default="5", help="setup timeframe(s): 1 | 5 | 15 | 60 | both (5m+15m) | all | comma list e.g. 1,5,60")
    ap.add_argument("--top", type=int, default=5, help="max setups shown per scan")
    ap.add_argument("--watch", type=int, default=0, help="re-scan every N seconds")
    ap.add_argument("--htf", action="store_true", help="tag setups as aligned/counter-trend to HTF bias")
    ap.add_argument("--csv", default="", help="append setups to this CSV")
    ap.add_argument("--suffix", default="", help="MT5 symbol suffix, e.g. m")
    ap.add_argument("--mt5-offset", type=float, default=0, help="broker server time minus UTC, hours")
    ap.add_argument("--seed", default=0, help="demo data seed")
    args = ap.parse_args()
    seen = set()
    while True:
        run_once(args, seen)
        if not args.watch:
            break
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
