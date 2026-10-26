#!/usr/bin/env python3
"""
ICT Top-Down Workflow  (analysis only, it does NOT place trades)

One pair at a time, walked from the big picture to the trigger. Every step must pass before the next one counts:

  GATE  News          no high-impact release about to hit (ict_news.py)
  1     Daily / Weekly  bias, premium / discount, draw on liquidity       (ict_htf_bias.py)
  2     4H            point of interest (POI): unmitigated FVG / order block in the direction of bias,
                      in discount for longs / premium for shorts, and has price tapped it?
  3     1H            confirmation at that POI: liquidity sweep + displacement + MSS,
                      plus the refined 1H zone (FVG / OB) and the OTE band (62-79%)
  4     1M            trigger inside a killzone: the scanner's 1-minute ICT models, same direction as the bias,
                      tagged with whether the entry sits inside the 1H zone (confluence)

Verdict: TRADE READY / WAIT (and exactly which step is missing) / NO TRADE.
Core rule: higher timeframes give direction, lower timeframes give timing. Never take a 1M signal against the 4H / Daily bias.

Run :  python ict_topdown.py                         (Yahoo data, all 4 pairs)
       python ict_topdown.py --symbols XAUUSD
       python ict_topdown.py --source mt5 --mt5-offset 3 --suffix m
       python ict_topdown.py --source demo           (synthetic data: stages are independent random series, pipeline test only)
Needs: pip install pandas numpy yfinance   (keep ict_htf_bias.py, ict_scanner.py, ict_news.py in the same folder)
Educational tool. Not financial advice.
"""
import argparse
import time

import numpy as np
import pandas as pd

import ict_htf_bias as hb
import ict_news as nw
import ict_scanner as sc

NY = hb.NY
H1_TTL = 900                      # the 1H history (200 days) is slow to download: reuse it for 15 minutes
_h1 = {}


def get_h1(sym, args):
    key = (args.source, sym, getattr(args, "suffix", ""), getattr(args, "mt5_offset", 0), str(getattr(args, "seed", 0)))
    hit = _h1.get(key)
    if hit and time.time() - hit[0] < H1_TTL:
        return hit[1]
    df = hb.LOADERS[args.source](sym, args)
    _h1[key] = (time.time(), df)
    return df


def view(df, sgn):
    """Everything below is written as a BULLISH view. Bearish = the same logic on a mirrored copy (as in the scanner)."""
    if sgn > 0:
        return df
    return pd.DataFrame({"open": -df.open, "high": -df.low, "low": -df.high, "close": -df.close}, index=df.index)


def stage(n, name, tf, status, summary, details=None, **extra):
    return dict(n=n, name=name, tf=tf, status=status, summary=summary, details=details or [], **extra)


# ------------------------------------------------------------------ steps ----
def step_bias(b):
    det = [b["lines"][i] for i in (0, 1, 2, 3, 4)] + [b["lines"][5], b["lines"][6]]
    if b["inv"]:
        det.append(b["inv"])
    if b["dir"] == 0:
        return stage(1, "Bias", "Daily / Weekly", "fail", f"No HTF bias (score {b['score']:+d}/5). Stand aside or trade range extremes with reduced size.", det)
    word = "longs" if b["dir"] > 0 else "shorts"
    good = (b["dir"] > 0 and b["zone"] == "DISCOUNT") or (b["dir"] < 0 and b["zone"] == "PREMIUM")
    note = (f"Price is in {b['zone']}: good area for {word}." if good else
            f"Price is in {b['zone']}: only take {word} from a POI in the {'discount' if b['dir'] > 0 else 'premium'} half "
            f"(equilibrium {b['eq']:.{b['dig']}f}).")
    return stage(1, "Bias", "Daily / Weekly", "pass", f"{b['label']} ({b['conf']}, {b['score']:+d}/5). Look for {word}. {note}", det)


def poi_candidates(h4v, a4):
    """Bullish-view 4H points of interest: unmitigated FVGs and order blocks. Returns (kind, lo, hi)."""
    out = [("4H FVG", lo, hi) for k, lo, hi in hb.fvgs(h4v) if k == "bull"]
    o, h, l, c = (h4v[k].values for k in ("open", "high", "low", "close"))
    for i in range(max(1, len(h4v) - 40), len(h4v) - 2):
        # last down candle before an up candle that closes above it with displacement, never closed through since
        if c[i] < o[i] and c[i + 1] > h[i] and (h[i + 1] - l[i + 1]) >= a4 and not (c[i + 2:] < l[i]).any():
            out.append(("4H OB", float(l[i]), float(h[i])))
    return out


def step_4h(H4, h1c, price, sgn, eq, p, dig):
    h4v, h1v = view(H4, sgn), view(h1c, sgn)
    pv, eqv = sgn * price, sgn * eq
    a4 = hb.atr(h4v.iloc[:-1])
    below = [x for x in poi_candidates(h4v, a4) if x[1] <= pv]
    if not below:
        return stage(2, "4H POI", "4H", "wait", "No unmitigated 4H FVG / order block on the trade side of price. Wait for the 4H to print one.", []), None
    disc = [x for x in below if x[2] <= eqv]
    kind, zlo, zhi = max(disc or below, key=lambda x: x[2])           # nearest to price, discount preferred
    in_disc = zhi <= eqv
    tapped = h1v.iloc[-12:].low.min() <= zhi                           # any of the last 12 hourly bars traded into the zone
    dist = (pv - zhi) / a4
    z = sorted([sgn * zlo, sgn * zhi])
    half = ("discount" if in_disc else "premium") if sgn > 0 else ("premium" if in_disc else "discount")
    det = [f"{kind} {p(zlo)}-{p(zhi)} ({half} of the 20-day range).",
           f"Invalidation: a 4H close beyond {p(zlo)} against the bias.",
           "Targets: opposing 4H / Daily liquidity (see the bias card)."]
    if not in_disc:
        det.append("Caution: no POI in the favourable half of the range, so this one is lower quality.")
    if tapped and in_disc:
        s = stage(2, "4H POI", "4H", "pass", f"Price has tapped the {kind} {p(zlo)}-{p(zhi)} ({'discount' if sgn > 0 else 'premium'}) within the last 12 hours.", det)
    elif tapped:
        s = stage(2, "4H POI", "4H", "wait",
                  f"Price tapped the {kind} {p(zlo)}-{p(zhi)}, but it is in the wrong half of the range ({'premium' if sgn > 0 else 'discount'} "
                  f"for {'longs' if sgn > 0 else 'shorts'}). Wait for a POI in {'discount' if sgn > 0 else 'premium'}.", det)
    else:
        s = stage(2, "4H POI", "4H", "wait", f"{kind} {p(zlo)}-{p(zhi)} is {dist:.1f} 4H-ATR away. Wait for price to trade into it.", det)
    poi = dict(kind=kind, lo=float(zlo), hi=float(zhi), lo_raw=float(z[0]), hi_raw=float(z[1]), tapped=bool(tapped), in_disc=bool(in_disc))
    return s, poi


def step_1h(h1c, poi, sgn, p):
    d = view(h1c, sgn).iloc[-150:]
    N = len(d)
    a1 = hb.atr(d)
    h, l, c, o = (d[k].values for k in ("high", "low", "close", "open"))
    sh, sl = hb.swing_pts(d)
    best = None
    for i, v in sl:                                                    # latest sweep of a 1H swing low near the POI
        if i < N - 40:
            continue
        k = next((x for x in range(i + 1, N) if l[x] < v), None)
        if k is None or k < N - 30 or l[k] > poi["hi"] + 0.5 * a1:
            continue
        if best is None or k > best[0]:
            best = (k, v)
    if best is None:
        return stage(3, "1H confirmation", "1H", "wait",
                     "No 1H liquidity sweep at the POI yet. Wait for a stop run through a 1H swing " + ("low." if sgn > 0 else "high."),
                     ["Sequence needed: sweep -> displacement (impulsive candles leaving FVGs) -> MSS (close through the last opposing 1H swing)."]), None
    k, swept = best
    prior = [v for j, v in sh if j < k]
    ref = prior[-1] if prior else None
    j = next((x for x in range(k + 1, min(k + 13, N)) if ref is not None and c[x] > ref), None)
    sweep_txt = f"Swept the 1H swing {'low' if sgn > 0 else 'high'} {p(swept)} at {d.index[k]:%a %H:%M} NY."
    if j is None:
        return stage(3, "1H confirmation", "1H", "wait", sweep_txt + " No MSS yet (needs a 1H close beyond " +
                     (p(ref) if ref is not None else "the last opposing swing") + ").", [sweep_txt]), None
    leg_lo, ext = l[k:j + 1].min(), h[k:j + 1].max()
    big = (h[k:j + 1] - l[k:j + 1]).max() >= 1.2 * a1 and (ext - leg_lo) >= 1.5 * a1
    det = [sweep_txt, f"MSS: 1H close {d.index[j]:%a %H:%M} beyond {p(ref)}.",
           f"Displacement leg {p(leg_lo)} -> {p(ext)} ({(ext - leg_lo) / a1:.1f} 1H-ATR) - " + ("strong." if big else "weak (under 1.5 ATR or no impulsive candle).")]
    zone = None
    for x in range(j, k, -1):                                          # refined zone = newest unmitigated 1H FVG in the leg
        if x >= 2 and l[x] > h[x - 2] and (l[x] - h[x - 2]) >= 0.1 * a1 and not (c[x + 1:] < h[x - 2]).any():
            zone = ("1H FVG", float(h[x - 2]), float(l[x]))
            break
    if zone is None:                                                   # else the last down candle before the strongest up candle
        m = k + int(np.argmax(c[k:j + 1] - o[k:j + 1]))
        ob = next((x for x in range(m - 1, k - 4, -1) if x >= 0 and c[x] < o[x]), None)
        if ob is not None and not (c[ob + 1:] < c[ob]).any():
            zone = ("1H OB", float(c[ob]), float(o[ob]))
    ote = (float(ext - 0.79 * (ext - leg_lo)), float(ext - 0.62 * (ext - leg_lo)))
    if zone:
        det.append(f"Refined entry zone: {zone[0]} {p(zone[1])}-{p(zone[2])} (50% = {p((zone[1] + zone[2]) / 2)}).")
    det.append(f"OTE band (62-79% retrace): {p(ote[0])}-{p(ote[1])}.")
    det.append(f"Invalidation: a 1H close beyond the swept extreme {p(leg_lo)}.")
    z = zone or ("OTE", ote[0], ote[1])
    info = dict(kind=z[0], lo=z[1], hi=z[2], ote=list(ote), leg_lo=float(leg_lo), a1=float(a1))
    st = "pass" if big else "wait"
    return stage(3, "1H confirmation", "1H", st,
                 ("Sweep + displacement + MSS confirmed. " if big else "Sweep and MSS found but displacement is weak. ") +
                 f"Refine entries inside {z[0]} {p(z[1])}-{p(z[2])}.", det), info


def kz_state(now=None):
    now = now or pd.Timestamp.now(tz=NY)
    closed = now.weekday() == 5 or (now.weekday() == 6 and now.hour < 17) or (now.weekday() == 4 and now.hour >= 17)
    name = ("London killzone" if 2 <= now.hour < 5 else "NY AM killzone" if 7 <= now.hour < 11 else
            "NY PM killzone" if 13 <= now.hour < 16 else "")
    return dict(closed=bool(closed), in_kz=bool(name) and not closed, name=name, silver_bullet=now.hour in sc.SB_HOURS and not closed,
                clock=f"{now:%a %H:%M} NY")


def step_1m(sym, args, sgn, h1info, a1, dig, kz):
    side = "buy" if sgn > 0 else "sell"
    f = lambda x: f"{x:.{dig}f}"
    df1 = sc.fetch(sym, args, 1)
    now = time.time()
    found = [s for s in sc.scan(sym, df1, "1m", "ict") if s.side == side and now - s.ts <= 3 * 3600]
    zlo, zhi = (min(sgn * h1info["lo"], sgn * h1info["hi"]), max(sgn * h1info["lo"], sgn * h1info["hi"])) if h1info else (None, None)
    rows = []
    for s in found:
        near = bool(zlo is not None and zlo - 0.5 * a1 <= s.entry <= zhi + 0.5 * a1)
        rows.append(dict(model=s.model, side=s.side, entry=s.entry, sl=s.sl, tp=s.tp, rr=s.rr, status=s.status, formed=s.formed,
                         score=s.score, notes=s.notes, in_1h_zone=near, ts=s.ts))
    rows.sort(key=lambda r: (-int(r["in_1h_zone"]), -r["score"], -r["rr"]))
    det = [f"Clock: {kz['clock']} - {'market closed' if kz['closed'] else kz['name'] or 'outside killzone'}"
           + (" + Silver Bullet window" if kz["silver_bullet"] else "") + "."]
    if not rows:
        return stage(4, "1M trigger", "1M", "wait",
                     f"No fresh 1-minute {side.upper()} model (sweep + MSS + FVG / OB / Silver Bullet) in the last 3 hours.",
                     det + ["Wait for: sweep of a 5M/15M or session low -> 1M displacement + MSS -> limit at the 1M FVG midpoint, stop beyond the sweep."]), []
    best = rows[0]
    det.append(f"Best: {best['model']} {side.upper()} limit {f(best['entry'])}, SL {f(best['sl'])}, TP {f(best['tp'])}, RR {best['rr']} [{best['status']}], formed {best['formed']} NY.")
    det.append("Entry is " + ("INSIDE" if best["in_1h_zone"] else "outside") + " the 1H zone" + ("." if best["in_1h_zone"] else " (lower confluence)."))
    det += [f"{r['model']}: {r['notes']}" for r in rows[:3]]
    st = "pass" if kz["in_kz"] else "wait"
    msg = f"{len(rows)} 1M {side.upper()} setup(s)" + ("" if kz["in_kz"] else ", but outside a killzone - treat as low quality") + "."
    return stage(4, "1M trigger", "1M", st, msg, det), rows


# --------------------------------------------------------------- workflow ----
def compute(sym, args):
    h1 = get_h1(sym, args)
    b = hb.compute(sym, args, df=h1)
    D, H4, W = hb.frames(h1)
    dig = b["dig"]
    p_raw = lambda x: f"{x:.{dig}f}"
    price = float(b["price"])
    kz = kz_state()
    stages, plan = [], None

    try:                                                               # gate: news
        r = nw.risk(nw.get_events(args.source), [sym])[sym]
        gate = dict(status=r["status"], msg=r["msg"])
    except Exception as e:
        gate = dict(status="UNKNOWN", msg=f"calendar unavailable ({e}). Check Forex Factory yourself.")
    stages.append(stage(0, "News gate", "Calendar", {"CLEAR": "pass", "CAUTION": "wait", "BLACKOUT": "fail"}.get(gate["status"], "wait"),
                        f"{gate['status']}: {gate['msg']}"))
    stages.append(step_bias(b))

    verdict, why = None, None
    if b["dir"] == 0:
        verdict, why = "NO TRADE", "No higher-timeframe bias."
        for n, nm, tf in ((2, "4H POI", "4H"), (3, "1H confirmation", "1H"), (4, "1M trigger", "1M")):
            stages.append(stage(n, nm, tf, "skip", "Skipped: needs a directional bias first."))
    else:
        sgn = 1 if b["dir"] > 0 else -1
        p = lambda x: f"{sgn * x:.{dig}f}"
        s2, poi = step_4h(H4, h1.iloc[:-1], price, sgn, b["eq"], p, dig)
        stages.append(s2)
        h1info = None
        if poi and poi["tapped"]:
            s3, h1info = step_1h(h1.iloc[:-1], poi, sgn, p)
        else:
            s3 = stage(3, "1H confirmation", "1H", "skip", "Skipped: price has not reached a 4H POI yet.")
        stages.append(s3)
        a1 = h1info["a1"] if h1info else hb.atr(h1.iloc[:-1].resample("1h").agg(hb.AGG).dropna().iloc[-150:])
        try:
            s4, rows = step_1m(sym, args, sgn, h1info, a1, dig, kz)
        except Exception as e:
            s4, rows = stage(4, "1M trigger", "1M", "skip", f"1-minute data unavailable: {e}"), []
        if not (poi and poi["tapped"] and h1info and s3["status"] == "pass"):
            s4["details"].insert(0, "Informational only: the 4H / 1H steps above are not complete, so do not take this yet.")
        stages.append(s4)
        ok = lambda n: stages[n]["status"] == "pass"
        if gate["status"] == "BLACKOUT":
            verdict, why = "NO TRADE", gate["msg"]
        elif not ok(2):
            verdict, why = "WAIT", ("No 4H POI available." if not poi else
                                    "Price has not tapped a 4H POI yet." if not poi["tapped"] else
                                    "The tapped 4H POI is in the wrong half of the range (premium/discount rule).")
        elif not ok(3):
            verdict, why = "WAIT", "Waiting for 1H sweep + displacement + MSS at the POI."
        elif kz["closed"]:
            verdict, why = "WAIT", "Market is closed."
        elif not kz["in_kz"]:
            verdict, why = "WAIT", "Outside a killzone. Wait for London / NY AM / NY PM."
        elif not rows:
            verdict, why = "WAIT", "No 1-minute trigger yet."
        else:
            best = rows[0]
            verdict, why = "TRADE READY", ("1M trigger inside the 1H zone." if best["in_1h_zone"] else "1M trigger found, but outside the 1H zone (lower confluence).")
            if gate["status"] == "CAUTION":
                why += " News caution: plan to be flat before the release."
            plan = dict(side=best["side"], model=best["model"], entry=best["entry"], sl=best["sl"], tp=best["tp"], rr=best["rr"],
                        status=best["status"], risk_note="Risk 0.5-1% per trade. Move to breakeven after TP1 / first strong displacement.")
    passed = sum(1 for s in stages if s["status"] == "pass")
    return dict(sym=sym, price=price, dig=dig, label=b["label"], dir=b["dir"], verdict=verdict, why=why, plan=plan, stages=stages,
                passed=passed, total=len(stages), kz=kz, updated=time.strftime("%H:%M:%S"))


MARK = {"pass": "[x]", "wait": "[ ]", "fail": "[!]", "skip": "[-]"}


def show(r):
    print(f"\n=== {r['sym']} {r['price']:.{r['dig']}f} | {r['verdict']} ({r['passed']}/{r['total']} steps) - {r['why']} ===")
    for s in r["stages"]:
        print(f"  {MARK[s['status']]} {s['n']}. {s['name']} ({s['tf']}): {s['summary']}")
        for d in s["details"]:
            print(f"        - {d}")
    if r["plan"]:
        pl, f = r["plan"], lambda x: f"{x:.{r['dig']}f}"
        print(f"  >>> {pl['side'].upper()} LIMIT {f(pl['entry'])}  SL {f(pl['sl'])}  TP {f(pl['tp'])}  RR {pl['rr']}  [{pl['status']}]  ({pl['model']})")
        print(f"      {pl['risk_note']}")


def main():
    ap = argparse.ArgumentParser(description="ICT top-down workflow: bias -> 4H POI -> 1H confirmation -> 1M trigger")
    ap.add_argument("--source", choices=hb.LOADERS, default="yahoo")
    ap.add_argument("--symbols", nargs="+", default=list(hb.YAHOO))
    ap.add_argument("--suffix", default="")
    ap.add_argument("--mt5-offset", type=float, default=0, help="broker server time minus UTC, hours")
    ap.add_argument("--seed", default=0)
    args = ap.parse_args()
    print(f"Top-down workflow  {pd.Timestamp.now(tz=NY):%a %Y-%m-%d %H:%M} NY   [x] pass  [ ] waiting  [!] blocked  [-] skipped")
    for sym in args.symbols:
        try:
            show(compute(sym, args))
        except Exception as e:
            print(f"\n! {sym}: {e}")


if __name__ == "__main__":
    main()
