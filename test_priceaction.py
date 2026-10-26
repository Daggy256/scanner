import numpy as np, pandas as pd, sys
import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ict_priceaction as pa, ict_scanner as sc

NY = sc.NY
def frame(rows):
    idx = pd.date_range(end=pd.Timestamp.now(tz=NY).floor("5min"), periods=len(rows), freq="5min")
    a = np.array(rows, float)
    return pd.DataFrame(dict(open=a[:,0], high=a[:,1], low=a[:,2], close=a[:,3]), index=idx)
def from_closes(closes, wick=0.1):
    closes = np.asarray(closes, float); o = np.r_[closes[0], closes[:-1]]
    return [[o[i], max(o[i], closes[i]) + wick, min(o[i], closes[i]) - wick, closes[i]] for i in range(len(closes))]
def sine(n, center, amp=2.0, per=24, t0=0):
    t = np.arange(n) + t0
    return center + amp * np.sin(2*np.pi*t/per)
def flip(df):   # mirror around 200 -> bearish twin
    return pd.DataFrame(dict(open=200-df.open, high=200-df.low, low=200-df.high, close=200-df.close), index=df.index)
def models(df, sym="EURUSD"):
    s, r = pa.analyze(sym, df, "5m"); return s, r
def show(tag, setups):
    print(f"  {tag}:", [(s.model, s.side, s.trigger, round(s.rr,1), s.status, s.score) for s in setups] or "none")

fails = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra and not cond else ""))
    if not cond: fails.append(name)

# ---------- 1) Zone reaction: bullish pin at a 10x support level
rows = from_closes(sine(259, 100.0))                       # trough at the last bar
rows[-1] = [98.05, 98.2, 97.7, 98.15]                       # pin: lower wick 70%, body 20%
df = frame(rows); s, _ = models(df); show("zone/pin bull", s)
z = [x for x in s if x.model == "Zone reaction" and x.side == "buy"]
check("1a Zone reaction BUY at support (pin)", len(z) == 1 and z[0].status == "PENDING" and z[0].rr > 3 and "pin" in z[0].trigger.lower())
s2, _ = models(flip(df)); show("zone/pin bear", s2)
z2 = [x for x in s2 if x.model == "Zone reaction" and x.side == "sell"]
check("1b mirrored: Zone reaction SELL at resistance", len(z2) == 1 and "Bearish" in z2[0].trigger and z2[0].entry < z2[0].zlo + 5)

# ---------- 1c) negative: same pin in the middle of the range (no level) -> nothing
rows = from_closes(sine(259 - 6, 100.0) )
rows += [[100.0, 100.0, 100.0, 100.0]] * 0
rows = from_closes(sine(260, 100.0, t0=0)); mid = 6          # at t=260: sin(2pi*260/24)= sin(.. ) pick index where price ~100 rising
k = next(i for i in range(len(rows)-1, 200, -1) if abs(rows[i][3] - 100) < 0.12)
rows = rows[:k] + [[100.0, 100.15, 99.65, 100.1]]
s, _ = models(frame(rows)); z = [x for x in s if x.model == "Zone reaction"]
check("1c pin bar in mid-range is ignored", len(z) == 0, str([(x.model, x.zlo, x.zhi) for x in z]))

# ---------- 2) False breakout (spring)
rows = from_closes(sine(256, 100.0))
rows += [[98.1, 98.15, 97.3, 97.4], [97.4, 97.9, 97.3, 97.8], [97.8, 98.5, 97.75, 98.4]]
df = frame(rows); s, _ = models(df); show("fakeout bull", s)
f = [x for x in s if x.model == "False breakout" and x.side == "buy"]
check("2a False breakout BUY (spring reclaimed)", len(f) == 1 and f[0].sl < 97.3 and f[0].rr >= 1.5)
s2, _ = models(flip(df)); f2 = [x for x in s2 if x.model == "False breakout" and x.side == "sell"]
check("2b mirrored: False breakout SELL (upthrust)", len(f2) == 1 and f2[0].sl > 102.7)
rows_n = rows[:-1] + [[97.8, 98.0, 97.7, 97.75]]            # no reclaim: closes still below the zone
s, _ = models(frame(rows_n)); check("2c no reclaim -> no False breakout", not [x for x in s if x.model == "False breakout"])

# ---------- shared: higher-ceiling history for trend models / break & retest
def with_ceiling_range():
    p1 = sine(70, 105.0)                                    # old range 103-107 -> resistance cluster ~107.1
    p2 = np.linspace(105, 100, 10)
    p3 = sine(170, 100.0, t0=0)                              # current range 98-102
    return np.r_[p1, p2, p3]

# ---------- 3) Break & retest
base = with_ceiling_range()
# end the sine at its trough then craft: rally to the resistance, displacement bar, retest, rejection
rows = from_closes(base)
rows += [[100.0, 100.3, 99.9, 100.2]]
# climb into 102.0 (previous swing highs ~102.1)
rows += from_closes([100.6, 101.1, 101.6, 101.95], 0.1)
rows[-4][0] = 100.2
rows += [[101.95, 103.3, 101.9, 103.2],                      # displacement close through ~102.15, body 1.25
         [103.2, 103.3, 102.7, 102.8],
         [102.8, 102.85, 102.3, 102.4],
         [102.4, 102.45, 102.1, 102.3],                      # retest touches the flipped zone, holds
         [102.25, 102.9, 102.2, 102.8]]                      # rejection candle closes above prior high
df = frame(rows); s, _ = models(df); show("retest bull", s)
r = [x for x in s if x.model == "Break & retest" and x.side == "buy"]
check("3a Break & retest BUY", len(r) == 1 and r[0].rr >= 1.5 and r[0].tp > r[0].entry and r[0].sl < 102.1 and r[0].status == "PENDING", str(r))
s2, _ = models(flip(df)); r2 = [x for x in s2 if x.model == "Break & retest" and x.side == "sell"]
check("3b mirrored: Break & retest SELL", len(r2) == 1)

# ---------- 4) With-trend pullback  (uptrend with a high swing in history as the target)
def uptrend(n=200):
    tent = np.r_[np.linspace(100, 140, 40), np.linspace(140, 100, 30)]
    t = np.arange(n); tr = 100 + 0.12 * t + 0.8 * np.sin(2*np.pi*t/20)
    return np.r_[tent, tr]
closes = uptrend(); rows = from_closes(closes)
# find a pullback trough in the last 60 bars and truncate there
t_idx = [i for i in range(len(closes)-60, len(closes)-2) if closes[i] < closes[i-1] and closes[i] < closes[i+1]]
k = t_idx[-1]; rows = rows[:k+1]
c = np.array([r[3] for r in rows]); e20 = pd.Series(c).ewm(span=20, adjust=False).mean().values[-2]
rows[-2] = [e20 + 0.35, e20 + 0.4, e20 - 0.05, e20 + 0.0]     # bearish bar into EMA20
rows[-1] = [e20 - 0.02, e20 + 0.67, e20 - 0.05, e20 + 0.62]    # bullish engulfing, closes back above EMA20
df = frame(rows); s, _ = models(df); show("pullback bull", s)
p = [x for x in s if x.model == "With-trend pullback" and x.side == "buy"]
check("4a With-trend pullback BUY", len(p) == 1 and "engulfing" in p[0].trigger.lower(), str(p))
s2, _ = models(flip(df)); p2 = [x for x in s2 if x.model == "With-trend pullback" and x.side == "sell"]
check("4b mirrored: With-trend pullback SELL", len(p2) == 1)
# negative: same engulfing but in a DOWNtrend must not give a with-trend BUY
down = flip(frame(from_closes(uptrend()))); 
check("4c no with-trend BUY in a downtrend", not [x for x in models(down)[0] if x.model == "With-trend pullback" and x.side == "buy"])

# ---------- 5) Inside-bar breakout in the same uptrend
rows = from_closes(uptrend())
k = t_idx[-1]; rows = rows[:k + 1]
c = np.array([r[3] for r in rows]); e = pd.Series(c).ewm(span=20, adjust=False).mean().values[-4]
rows[-3] = [e + 0.8, e + 0.9, e - 0.35, e + 0.0]               # mother bar (1.25 range)
rows[-2] = [e + 0.3, e + 0.55, e - 0.1, e + 0.4]               # inside bar
rows[-1] = [e + 0.4, e + 1.1, e + 0.35, e + 1.05]              # breakout above mother high
df = frame(rows); s, _ = models(df); show("insidebar bull", s)
ib = [x for x in s if x.model == "Inside-bar breakout" and x.side == "buy"]
check("5a Inside-bar breakout BUY", len(ib) == 1, str(ib))
s2, _ = models(flip(df)); check("5b mirrored: Inside-bar breakout SELL", len([x for x in s2 if x.model == "Inside-bar breakout" and x.side == "sell"]) == 1)

# ---------- 6) order status handling (reuse test 1 pattern)
base_rows = from_closes(sine(259, 100.0)); base_rows[-1] = [98.05, 98.2, 97.7, 98.15]
def zr(extra):
    s, _ = models(frame(base_rows + extra)); return [x for x in s if x.model == "Zone reaction" and x.side == "buy"]
e0 = zr([])[0].entry
a = zr([[98.15, e0 + 0.2, 98.1, e0 + 0.1]])
check("6a stop filled next bar -> ACTIVE", len(a) == 1 and a[0].status == "ACTIVE", str(a))
check("6b SL hit before entry -> setup dropped", zr([[98.1, 98.15, 97.0, 97.2]]) == [])
sl = zr([])[0].sl; tp = zr([])[0].tp
check("6c stopped out after fill -> dropped", zr([[98.15, e0 + 0.2, 98.1, e0 + 0.1], [e0 + 0.1, e0 + 0.12, sl - 0.2, sl - 0.1]]) == [])
check("6d TP reached -> dropped", zr([[98.15, e0 + 0.2, 98.1, e0 + 0.1], [e0 + 0.1, tp + 0.3, e0, tp + 0.1]]) == [])
chase = zr([[98.15, 98.6, 98.1, 98.55], [98.55, 100.6, 98.5, 100.5]])      # filled and already > 1R in profit
check("6e already > 1R in profit -> too late, dropped", chase == [])
check("6f pending stop expires after 12 unfilled bars", zr([[98.0, 98.1, 97.95, 98.05]] * 14) == [])
check("6g ...but is still pending after 5 unfilled bars", len(zr([[98.0, 98.1, 97.95, 98.05]] * 5)) == 1)

# ---------- 7) no look-ahead: levels / zones as-of bar i identical on truncated vs full data
df = frame(from_closes(with_ceiling_range()))
full = pa.prep(df, 1, "EURUSD"); i = full.N - 40
cut = pa.prep(df.iloc[:i + 1], 1, "EURUSD")
lv_f, lv_c = pa.levels_asof(full, i), pa.levels_asof(cut, i)
check("7a levels_asof(i) uses no future bars", lv_f == lv_c)
zf = [z for z in pa.find_zones(full) if z["k"] < i - 4]; zc = [z for z in pa.find_zones(cut) if z["k"] < i - 4]
check("7b zones formed before i-4 identical on truncated data", zf == zc, f"{len(zf)} vs {len(zc)}")

print("\nFAILED:", fails if fails else "none")
sys.exit(1 if fails else 0)
