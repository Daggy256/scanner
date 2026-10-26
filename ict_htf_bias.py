#!/usr/bin/env python3
"""
ICT Higher-Timeframe Bias Analysis (weekly / daily / 4H)  (analysis only)

For each pair it reports:
  * Bias (bullish / bearish / neutral) with a score from -5 to +5
  * Weekly + daily candle logic (close beyond prior range, or sweep and reject)
  * Daily + 4H market structure (HH/HL vs LH/LL)
  * Price vs weekly open
  * Premium / discount of the 20-day dealing range
  * Nearest untaken liquidity above and below (draw on liquidity)
  * Nearest unmitigated daily / 4H fair value gaps
  * 1H structure + 1H fair value gaps (context for the 1H confirmation step, NOT scored)
  * Invalidation level and what to look for

Daily candles open at 17:00 NY, 4H candles at 01/05/09/13/17/21 NY.

Run :  python ict_htf_bias.py
       python ict_htf_bias.py --source mt5 --mt5-offset 3 --suffix m
       python ict_htf_bias.py --source demo
Needs: pip install pandas numpy yfinance
Educational tool. Not financial advice.
"""
import argparse

import numpy as np
import pandas as pd

NY = "America/New_York"
YAHOO = {"XAUUSD": "GC=F", "USDJPY": "USDJPY=X", "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X"}
DIGITS = {"XAUUSD": 2, "USDJPY": 3, "EURUSD": 5, "GBPUSD": 5}
AGG = {"open": "first", "high": "max", "low": "min", "close": "last"}


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


def load_yahoo(sym, args):
    import yfinance as yf
    return norm(yf.download(YAHOO[sym], period="200d", interval="1h", progress=False, auto_adjust=False))


def load_mt5(sym, args):
    import MetaTrader5 as mt5
    if not mt5.initialize():
        raise RuntimeError("MT5 terminal not running / not logged in")
    name = sym + args.suffix
    mt5.symbol_select(name, True)
    rates = mt5.copy_rates_from_pos(name, mt5.TIMEFRAME_H1, 0, 5000)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"no MT5 data for {name}")
    df = pd.DataFrame(rates)
    df.index = pd.to_datetime(df["time"], unit="s") - pd.Timedelta(hours=args.mt5_offset)
    return norm(df)


def load_demo(sym, args):
    rng = np.random.default_rng(sum(map(ord, sym)) + int(args.seed))
    base = {"XAUUSD": 2650.0, "USDJPY": 150.0, "EURUSD": 1.08, "GBPUSD": 1.27}[sym]
    n = 200 * 24
    idx = pd.date_range(end=pd.Timestamp.now(tz=NY).floor("1h"), periods=n, freq="1h")
    vol = base * 0.0012
    drift = np.repeat(rng.normal(0, vol * 0.12, n // 120 + 1), 120)[:n]
    close = base + np.cumsum(rng.normal(0, vol, n) + drift)
    open_ = np.r_[base, close[:-1]]
    high = np.maximum(open_, close) + np.abs(rng.normal(0, vol * 0.5, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, vol * 0.5, n))
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)


LOADERS = {"yahoo": load_yahoo, "mt5": load_mt5, "demo": load_demo}


def frames(df):
    # Daily candle = 17:00 NY to 17:00 NY: shift +7h so the day starts at 00:00, resample, shift back.
    sd = df.copy()
    sd.index = sd.index + pd.Timedelta(hours=7)
    cnt = sd.close.resample("1D").count()
    D = sd.resample("1D").agg(AGG).dropna()
    D = D[(cnt.reindex(D.index) >= 3) | (D.index == D.index[-1])]
    D.index = D.index - pd.Timedelta(hours=7)
    H4 = df.resample("4h", offset="1h").agg(AGG).dropna()
    sh = D.index + pd.Timedelta(hours=7)                 # 17:00 start -> trading date
    iso = sh.isocalendar()
    wk = (iso.year.astype(int) * 100 + iso.week.astype(int)).values
    g = D.groupby(wk)
    W = pd.DataFrame({"open": g.open.first(), "high": g.high.max(),
                      "low": g.low.min(), "close": g.close.last()}).reset_index(drop=True)
    return D, H4, W


# ------------------------------------------------------------ primitives ----
def swing_pts(df, n=2):
    w = 2 * n + 1
    hi, lo = df.high, df.low
    sh = np.where(hi.eq(hi.rolling(w, center=True).max()).values)[0]
    sl = np.where(lo.eq(lo.rolling(w, center=True).min()).values)[0]
    return [(int(i), hi.iloc[i]) for i in sh], [(int(i), lo.iloc[i]) for i in sl]


def structure(df, name, p):
    """df = completed bars only."""
    sh, sl = swing_pts(df)
    if len(sh) < 2 or len(sl) < 2:
        return 0, f"{name} structure: not enough swings"
    h1, h0, l1, l0 = sh[-1][1], sh[-2][1], sl[-1][1], sl[-2][1]
    last = df.close.iloc[-1]
    brk = (" Last close broke above the last swing high." if last > h1 else
           " Last close broke below the last swing low." if last < l1 else "")
    if h1 > h0 and l1 > l0:
        return 1, f"{name} structure: higher highs / higher lows (bullish). Last swing low {p(l1)}.{brk}"
    if h1 < h0 and l1 < l0:
        return -1, f"{name} structure: lower highs / lower lows (bearish). Last swing high {p(h1)}.{brk}"
    return 0, f"{name} structure: mixed / ranging (swing high {p(h1)}, swing low {p(l1)}).{brk}"


def candle_rule(y, y2, name, p):
    """y = last completed candle, y2 = the one before it."""
    if y.close > y2.high:
        return 1, f"{name}: last candle closed ABOVE the prior high {p(y2.high)} (bullish displacement)."
    if y.close < y2.low:
        return -1, f"{name}: last candle closed BELOW the prior low {p(y2.low)} (bearish displacement)."
    if y.high > y2.high and y.close <= y2.high:
        return -1, f"{name}: swept the prior high {p(y2.high)} but closed back inside (failed breakout, bearish)."
    if y.low < y2.low and y.close >= y2.low:
        return 1, f"{name}: swept the prior low {p(y2.low)} but closed back inside (rejection, bullish)."
    return 0, f"{name}: inside the prior candle's range (no clear signal)."


def fvgs(df, n_last=60):
    """Unmitigated FVGs: (kind, lo, hi). df includes the forming bar for the mitigation check."""
    h, l, c = df.high.values, df.low.values, df.close.values
    out = []
    for i in range(max(2, len(df) - n_last), len(df) - 1):
        if l[i] > h[i - 2] and not (c[i + 1:] < h[i - 2]).any():
            out.append(("bull", h[i - 2], l[i]))
        elif h[i] < l[i - 2] and not (c[i + 1:] > l[i - 2]).any():
            out.append(("bear", h[i], l[i - 2]))
    return out


def nearest_fvgs(df, price, p, name):
    f = fvgs(df)
    below = [x for x in f if x[0] == "bull" and x[1] <= price]
    above = [x for x in f if x[0] == "bear" and x[2] >= price]
    parts = []
    if below:
        x = max(below, key=lambda z: z[2])
        parts.append(f"bullish FVG {p(x[1])}-{p(x[2])}")
    if above:
        x = min(above, key=lambda z: z[1])
        parts.append(f"bearish FVG {p(x[1])}-{p(x[2])}")
    return f"{name}: " + (" | ".join(parts) if parts else "no unmitigated FVG near price")


# --------------------------------------------------------------- analysis ----
def compute(sym, args, df=None):
    """df = optional pre-loaded 1H frame (lets ict_topdown.py share one download)."""
    df = LOADERS[args.source](sym, args) if df is None else df
    D, H4, W = frames(df)
    if len(D) < 25 or len(W) < 4 or len(H4) < 30:
        raise RuntimeError("not enough history")
    dig = DIGITS[sym]
    p = lambda x: f"{x:.{dig}f}"
    price = df.close.iloc[-1]
    dc, h4c = D.iloc[:-1], H4.iloc[:-1]                # completed bars only
    today, y, y2 = D.iloc[-1], D.iloc[-2], D.iloc[-3]
    datr = atr(dc)

    s_w, t_w = candle_rule(W.iloc[-2], W.iloc[-3], "Weekly", p)
    s_d, t_d = candle_rule(y, y2, "Daily", p)
    s_ds, t_ds = structure(dc, "Daily", p)
    s_h4, t_h4 = structure(h4c, "4H", p)
    wopen = W.iloc[-1].open
    if abs(price - wopen) < 0.1 * datr:
        s_wo, t_wo = 0, f"Price {p(price)} is at the weekly open {p(wopen)}."
    elif price > wopen:
        s_wo, t_wo = 1, f"Price is ABOVE the weekly open {p(wopen)}."
    else:
        s_wo, t_wo = -1, f"Price is BELOW the weekly open {p(wopen)}."
    score = s_w + s_d + s_ds + s_h4 + s_wo
    dirn = int(np.sign(score))
    conf = {0: "no edge", 1: "low", 2: "moderate"}.get(abs(score), "high")
    label = "NEUTRAL" if dirn == 0 else ("BULLISH" if dirn > 0 else "BEARISH")

    s_h1, t_h1 = structure(df.iloc[:-1].iloc[-150:], "1H", p)     # context only, not part of the score

    # premium / discount of the 20-day dealing range
    rng = D.iloc[-20:]
    hi, lo = rng.high.max(), rng.low.min()
    pct = (price - lo) / (hi - lo) if hi > lo else 0.5
    eq = (hi + lo) / 2
    zone = "PREMIUM" if pct > 0.55 else "DISCOUNT" if pct < 0.45 else "EQUILIBRIUM"

    # draw on liquidity: untaken highs above / lows below
    wk_hi, wk_lo = W.iloc[-1].high, W.iloc[-1].low
    ups, dns = [], []
    if today.high < y.high:
        ups.append(("Prev-day high", y.high))
    if wk_hi < W.iloc[-2].high:
        ups.append(("Prev-week high", W.iloc[-2].high))
    if today.low > y.low:
        dns.append(("Prev-day low", y.low))
    if wk_lo > W.iloc[-2].low:
        dns.append(("Prev-week low", W.iloc[-2].low))
    sh, sl = swing_pts(dc)
    for i, v in sh:
        if D.high.iloc[i + 1:].max() < v:
            ups.append(("Daily swing high", v))
    for i, v in sl:
        if D.low.iloc[i + 1:].min() > v:
            dns.append(("Daily swing low", v))
    ups = sorted([u for u in ups if u[1] > price], key=lambda z: z[1])
    dns = sorted([d for d in dns if d[1] < price], key=lambda z: -z[1])
    up_s = (f"{ups[0][0]} {p(ups[0][1])} (+{(ups[0][1] - price) / datr:.1f} ATR)" if ups else "none found")
    dn_s = (f"{dns[0][0]} {p(dns[0][1])} (-{(price - dns[0][1]) / datr:.1f} ATR)" if dns else "none found")

    # invalidation and plan
    if dirn > 0:
        inv = f"Daily close below {p(sl[-1][1])} (last daily swing low) invalidates the bullish bias." if sl else ""
        plan = ("Look for LONGS: wait for a sell-side raid (Asia/London low or prev-day low) in a killzone, "
                "then MSS + FVG/OB/OTE entry. " +
                (f"Price is in {zone}: good area to buy." if zone == "DISCOUNT" else
                 f"Price is in {zone}: do not chase, wait for a pullback toward {p(eq)} or lower."))
    elif dirn < 0:
        inv = f"Daily close above {p(sh[-1][1])} (last daily swing high) invalidates the bearish bias." if sh else ""
        plan = ("Look for SHORTS: wait for a buy-side raid (Asia/London high or prev-day high) in a killzone, "
                "then MSS + FVG/OB/OTE entry. " +
                (f"Price is in {zone}: good area to sell." if zone == "PREMIUM" else
                 f"Price is in {zone}: do not chase, wait for a rally toward {p(eq)} or higher."))
    else:
        inv = ""
        plan = ("No clear HTF bias. Only trade reversals at range extremes (prev-day high/low) "
                "with reduced size, or stand aside.")

    lines = [
        t_w, t_d, t_ds, t_h4, t_wo,
        f"20-day range {p(lo)} - {p(hi)}: price at {pct * 100:.0f}% ({zone}), equilibrium {p(eq)}.",
        f"Draw on liquidity: above = {up_s}; below = {dn_s}.",
        nearest_fvgs(D, price, p, "Daily PD arrays"),
        nearest_fvgs(H4, price, p, "4H PD arrays"),
        t_h1 + " (context only, not scored)",
        nearest_fvgs(df.iloc[-240:], price, p, "1H PD arrays"),
        f"Daily ATR(14): {p(datr)}",
    ]
    return dict(sym=sym, price=price, score=score, dir=dirn, label=label, conf=conf,
                lines=lines, inv=inv, plan=plan, dig=dig,
                zone=zone, pct=float(pct), eq=float(eq), h1_dir=int(s_h1),
                up=(list(ups[0]) if ups else None), dn=(list(dns[0]) if dns else None))


def atr(d, n=14):
    pc = d.close.shift()
    tr = pd.concat([d.high - d.low, (d.high - pc).abs(), (d.low - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean().iloc[-1]


def show(b):
    print(f"\n=== {b['sym']}  {b['price']:.{b['dig']}f}  |  HTF BIAS: {b['label']} "
          f"({b['conf']}, score {b['score']:+d}/5) ===")
    for ln in b["lines"]:
        print(f"  - {ln}")
    if b["inv"]:
        print(f"  ! {b['inv']}")
    print(f"  > {b['plan']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=LOADERS, default="yahoo")
    ap.add_argument("--symbols", nargs="+", default=list(YAHOO))
    ap.add_argument("--suffix", default="")
    ap.add_argument("--mt5-offset", type=float, default=0, help="broker server time minus UTC, hours")
    ap.add_argument("--seed", default=0)
    args = ap.parse_args()
    now = pd.Timestamp.now(tz=NY)
    print(f"HTF bias report  {now:%a %Y-%m-%d %H:%M} NY")
    for sym in args.symbols:
        try:
            show(compute(sym, args))
        except Exception as e:
            print(f"\n! {sym}: {e}")


if __name__ == "__main__":
    main()
