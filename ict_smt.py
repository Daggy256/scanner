#!/usr/bin/env python3
"""
ICT SMT Divergence Scanner  (analysis only, it does NOT place trades)

SMT (smart money tool) divergence = two correlated markets disagree at a liquidity level.
One market takes the level (new low / high) while the other FAILS to. The one that failed is showing real
strength (at a low) or weakness (at a high), so the raid on the other is likely a stop run, not a trend change.

Markets compared:
  FX / gold (--market fx)
    positively correlated : EURUSD-GBPUSD, XAUUSD-EURUSD
    inversely correlated  : EURUSD-USDJPY, GBPUSD-USDJPY, XAUUSD-USDJPY   (the second leg is mirrored, so
                            "EURUSD new low" is compared with "USDJPY new HIGH" = dollar strength)
  US index futures (--market index)   NQ, ES, YM  (all move together)
    pairwise: NQ-ES, NQ-YM, ES-YM, plus TRIPLE SMT: when the three disagree (e.g. NQ and ES sweep the low, YM holds).
    Index SMT is classically read at the NY open (07:00-11:00 NY) and in the Silver Bullet windows.
    Bullish SMT -> buy the market that HELD; bearish SMT -> sell the market that held (it is the weak one).
  --market all (default) runs both.

Two kinds of divergence, on any of 1m / 5m / 15m / 1h:
  Level : the sweep happened at a key level  (previous-day high/low, Asia high/low, London high/low)
  Swing : the sweep happened against the recent range (last 12 bars vs the 48 bars before them)

Score 1-4:  +1 base, +1 sweep was reclaimed (price closed back inside), +1 inside a killzone, +1 at a key level.
Each divergence says which pair to lean buy / sell. Use it as a CONFIRMATION of a sweep (ICT models, top-down step 3/4),
never as a standalone entry.

Run :  python ict_smt.py                       (Yahoo data, 5m)
       python ict_smt.py --tf 1,5,15,60
       python ict_smt.py --market index --tf 1,5      (NQ / ES / YM only)
       python ict_smt.py --source mt5 --market index --mt5-map NQ=USTEC,ES=US500,YM=US30
       python ict_smt.py --source mt5 --mt5-offset 3 --suffix m
       python ict_smt.py --source demo         (synthetic data: the pairs are independent, so divergences are random)
Needs: pip install pandas numpy yfinance   (keep ict_scanner.py in the same folder)
Educational tool. Not financial advice.
"""
import argparse
import time

import numpy as np
import pandas as pd

import ict_scanner as sc

NY = sc.NY
FX = list(sc.YAHOO)
INDEX = list(sc.YAHOO_EXTRA)
ALL = FX + INDEX
# (A, B, rel): rel = +1 moves together, -1 moves opposite
GROUPS = [("EURUSD", "GBPUSD", 1), ("XAUUSD", "EURUSD", 1),
          ("EURUSD", "USDJPY", -1), ("GBPUSD", "USDJPY", -1), ("XAUUSD", "USDJPY", -1),
          ("NQ", "ES", 1), ("NQ", "YM", 1), ("ES", "YM", 1)]
LOOK, RECENT = 60, 12            # swing mode: reference window = bars [-60:-12], recent window = last 12 bars
MIN_TAKE, MIN_HOLD = 0.05, 0.03  # ATR fractions: the taker must really break, the holder must really not reach


def mirror(df):
    return pd.DataFrame({"open": -df.open, "high": -df.low, "low": -df.high, "close": -df.close}, index=df.index)


def day_levels(d):
    """Key levels in the given (possibly mirrored) frame: name -> (kind, value, start). 'low' = a level to be swept below."""
    days = d.index.normalize().unique()
    if len(days) < 2:
        return {}
    today, prev = days[-1], days[-2]
    out = {}
    pdd = d[d.index.normalize() == prev]
    if not pdd.empty:
        out["PDL"] = ("low", float(pdd.low.min()), today)
        out["PDH"] = ("high", float(pdd.high.max()), today)
    asia = d[(d.index >= today - pd.Timedelta(hours=4)) & (d.index < today)]
    if not asia.empty:
        out["Asia L"] = ("low", float(asia.low.min()), today)
        out["Asia H"] = ("high", float(asia.high.max()), today)
    lon = d[(d.index >= today + pd.Timedelta(hours=2)) & (d.index < today + pd.Timedelta(hours=5))]
    if not lon.empty and d.index[-1] >= today + pd.Timedelta(hours=5):
        out["London L"] = ("low", float(lon.low.min()), today + pd.Timedelta(hours=5))
        out["London H"] = ("high", float(lon.high.max()), today + pd.Timedelta(hours=5))
    return out


def _extreme(frame, kind):
    """(value, timestamp) of the lowest low / highest high of a frame."""
    if kind == "low":
        i = int(np.argmin(frame.low.values))
        return float(frame.low.values[i]), frame.index[i]
    i = int(np.argmax(frame.high.values))
    return float(frame.high.values[i]), frame.index[i]


def _build(tf, na, nb, rel, kind, label, kind_word, ref_a, ref_b, ext_a, ext_b, took_a, t_ext, last_close_a, last_close_b, a_atr, now):
    """Assemble one divergence. Everything arrives in the 'view' (B mirrored when rel = -1); B prices are un-mirrored here."""
    bull = kind == "low"
    side = "bullish" if bull else "bearish"
    taker, holder = (na, nb) if took_a else (nb, na)
    tk_ref, tk_ext = (ref_a, ext_a) if took_a else (rel * ref_b, rel * ext_b)
    ho_ref, ho_ext = (rel * ref_b, rel * ext_b) if took_a else (ref_a, ext_a)
    # words used for the REAL prices of each pair
    wa = "low" if bull else "high"
    wb = wa if rel > 0 else ("high" if bull else "low")
    tk_word, ho_word = (wa, wb) if took_a else (wb, wa)
    reclaimed = bool((last_close_a > ref_a) if (took_a and bull) else (last_close_a < ref_a) if took_a else
                     (last_close_b > ref_b) if bull else (last_close_b < ref_b))
    hour = t_ext.hour
    in_kz = hour in sc.KZ_HOURS
    sb = hour in sc.SB_HOURS
    is_level = kind_word == "Level"
    lean = {na: "buy" if bull else "sell", nb: ("buy" if bull else "sell") if rel > 0 else ("sell" if bull else "buy")}
    age = max(0, int((now - t_ext.timestamp()) / 60))
    fa = lambda s, x: f"{x:.{sc.DIGITS[s]}f}"
    note = (f"{taker} swept {label} {tk_word} {fa(taker, tk_ref)} (to {fa(taker, tk_ext)}) while {holder} did NOT take its "
            f"{ho_word} {fa(holder, ho_ref)} (extreme {fa(holder, ho_ext)}). "
            f"{'Sweep reclaimed: price closed back inside. ' if reclaimed else 'Sweep not reclaimed yet. '}"
            f"Lean: " + ", ".join(f"{s} {v.upper()}" for s, v in lean.items()) + ".")
    return dict(pair=f"{na}/{nb}", a=na, b=nb, rel=rel, tf=tf, side=side, kind=kind_word, level=label, taker=taker, holder=holder,
                lean=lean, reclaimed=reclaimed, in_kz=in_kz, silver_bullet=sb, market="Index" if na in INDEX else "FX", score=1 + int(reclaimed) + int(in_kz) + int(is_level),
                formed=t_ext.strftime("%a %H:%M"), age_min=age, ts=int(t_ext.timestamp()), notes=note)


def detect(na, nb, rel, A, B, tf, now=None, max_age_h=8):
    """All divergences between A and B on one timeframe (A, B = completed bars of the same timeframe)."""
    now = now or time.time()
    idx = A.index.intersection(B.index)
    if len(idx) < LOOK + 5:
        return []
    A, B = A.loc[idx], B.loc[idx]
    Bv = B if rel > 0 else mirror(B)
    atr_a, atr_b = sc.atr(A).iloc[-1], sc.atr(Bv).iloc[-1]
    if not (np.isfinite(atr_a) and np.isfinite(atr_b) and atr_a > 0 and atr_b > 0):
        return []
    ca, cb = float(A.close.iloc[-1]), float(Bv.close.iloc[-1])
    out = []

    # ---- level divergences
    la, lb = day_levels(A), day_levels(Bv)
    for name in la.keys() & lb.keys():
        kind, va, start = la[name]
        _, vb, _ = lb[name]
        ra, rb = A[A.index >= start], Bv[Bv.index >= start]
        if ra.empty or rb.empty:
            continue
        ea, ta = _extreme(ra, kind)
        eb, tb = _extreme(rb, kind)
        sign = 1 if kind == "low" else -1
        took_a, took_b = sign * (va - ea) > MIN_TAKE * atr_a, sign * (vb - eb) > MIN_TAKE * atr_b
        held_a, held_b = sign * (ea - va) > MIN_HOLD * atr_a, sign * (eb - vb) > MIN_HOLD * atr_b
        if (took_a and held_b) or (took_b and held_a):
            t = ta if took_a else tb
            out.append(_build(tf, na, nb, rel, kind, name, "Level", va, vb, ea, eb, took_a, t, ca, cb, atr_a, now))

    # ---- swing divergences (recent window vs the range before it)
    for kind in ("low", "high"):
        ref_a_f, rec_a_f = A.iloc[-LOOK:-RECENT], A.iloc[-RECENT:]
        ref_b_f, rec_b_f = Bv.iloc[-LOOK:-RECENT], Bv.iloc[-RECENT:]
        va, ea_t = _extreme(ref_a_f, kind)
        vb, eb_t = _extreme(ref_b_f, kind)
        ea, ta = _extreme(rec_a_f, kind)
        eb, tb = _extreme(rec_b_f, kind)
        sign = 1 if kind == "low" else -1
        took_a, took_b = sign * (va - ea) > MIN_TAKE * atr_a, sign * (vb - eb) > MIN_TAKE * atr_b
        held_a, held_b = sign * (ea - va) > MIN_HOLD * atr_a, sign * (eb - vb) > MIN_HOLD * atr_b
        if (took_a and held_b) or (took_b and held_a):
            t = ta if took_a else tb
            out.append(_build(tf, na, nb, rel, kind, f"{LOOK - RECENT}-bar", "Swing", va, vb, ea, eb, took_a, t, ca, cb, atr_a, now))

    return [d for d in out if d["age_min"] <= max_age_h * 60]


def triples(divs):
    """Triple SMT across NQ / ES / YM: at the same reference, some markets took the liquidity and the rest held."""
    groups = {}
    for d in divs:
        if d["a"] in INDEX and d["b"] in INDEX:
            groups.setdefault((d["tf"], d["kind"], d["level"], d["side"]), []).append(d)
    out = []
    for (tf, kind, level, side), ds in groups.items():
        takers, holders = {d["taker"] for d in ds}, {d["holder"] for d in ds}
        if takers & holders or len(takers | holders) < 3:
            continue
        best = max(ds, key=lambda d: (d["score"], -d["age_min"]))
        act = "buy" if side == "bullish" else "sell"
        tk, ho = sorted(takers), sorted(holders)
        word = "low" if side == "bullish" else "high"
        out.append(dict(tf=tf, kind=kind, level=level, side=side, takers=tk, holders=ho, action=act,
                        score=best["score"], formed=best["formed"], age_min=best["age_min"], ts=best["ts"],
                        in_kz=best["in_kz"], silver_bullet=best["silver_bullet"], reclaimed=best["reclaimed"],
                        notes=(f"TRIPLE SMT on {kind.lower()} {level}: {' & '.join(tk)} took the {word}, {' & '.join(ho)} held. "
                               f"The market that held ({' & '.join(ho)}) is the {'strong' if side == 'bullish' else 'weak'} one: "
                               f"lean {act.upper()} {' & '.join(ho)}.")))
    out.sort(key=lambda t: (-t["score"], t["age_min"]))
    return out


def run(args, symbols=None, tfs=(5,), max_age_h=8, market="all"):
    """Scan every configured pair on the requested timeframes. Returns {divs, triples, errors, kz, scanned}."""
    symbols = symbols or {"fx": FX, "index": INDEX}.get(market, ALL)
    cache, errors, divs = {}, [], []

    def get(sym, m):
        key = (sym, m)
        if key not in cache:
            raw = cache.setdefault(("raw", sym, sc.BASE[m]), None)
            if raw is None:
                raw = sc.LOADERS[args.source](sym, args, sc.BASE[m])
                cache[("raw", sym, sc.BASE[m])] = raw
            cache[key] = sc.to_tf(raw, m)                 # completed bars only
        return cache[key]

    now = time.time()
    for a, b, rel in GROUPS:
        if a not in symbols or b not in symbols:
            continue
        for m in tfs:
            try:
                divs += detect(a, b, rel, get(a, m), get(b, m), sc.TF_LABEL[m], now, max_age_h)
            except Exception as e:
                errors.append(f"{a}/{b} {sc.TF_LABEL[m]}: {e}")
    divs.sort(key=lambda d: (-d["score"], d["age_min"]))
    ny = pd.Timestamp.now(tz=NY)
    return dict(divs=divs, triples=triples(divs), errors=errors, scanned=time.strftime("%H:%M:%S"), clock=f"{ny:%a %H:%M} NY",
                in_kz=bool(ny.hour in sc.KZ_HOURS))


def confirm(sym, side, args, tfs=(1, 5), min_score=2):
    """Hook for other tools: SMT divergences that support `side` ('buy' / 'sell') on `sym`, best first."""
    r = run(args, None, tfs)
    return [d for d in r["divs"] if d["lean"].get(sym) == side and d["score"] >= min_score]


def show_triple(t):
    print(f"[TRIPLE] {t['tf']} | {t['side'].upper()} SMT | {t['kind']}: {t['level']} | formed {t['formed']} ({t['age_min']}m ago)"
          f"{' | KILLZONE' if t['in_kz'] else ''}{' | SILVER BULLET WINDOW' if t['silver_bullet'] else ''}")
    print(f"       {t['notes']}")


def show(d):
    print(f"[{d['score']}/4] {d['pair']} {d['tf']} | {d['side'].upper()} SMT | {d['kind']}: {d['level']} | formed {d['formed']} ({d['age_min']}m ago)"
          f"{' | KILLZONE' if d['in_kz'] else ''}{' | SILVER BULLET WINDOW' if d['silver_bullet'] else ''}")
    print(f"       {d['notes']}")


def main():
    ap = argparse.ArgumentParser(description="ICT SMT divergence between correlated pairs")
    ap.add_argument("--source", choices=sc.LOADERS, default="yahoo")
    ap.add_argument("--market", choices=["fx", "index", "all"], default="all", help="FX/gold pairs, US index futures (NQ ES YM), or both")
    ap.add_argument("--symbols", nargs="+", default=None, help=f"override the market choice, any of: {' '.join(ALL)}")
    ap.add_argument("--mt5-map", default="", help="broker names for MT5, e.g. NQ=USTEC,ES=US500,YM=US30")
    ap.add_argument("--tf", default="5", help="1 | 5 | 15 | 60 | both | all | comma list e.g. 1,5")
    ap.add_argument("--max-age", type=float, default=8, help="ignore divergences older than N hours")
    ap.add_argument("--min-score", type=int, default=1)
    ap.add_argument("--watch", type=int, default=0, help="re-scan every N seconds, print only NEW divergences")
    ap.add_argument("--suffix", default="")
    ap.add_argument("--mt5-offset", type=float, default=0, help="broker server time minus UTC, hours")
    ap.add_argument("--seed", default=0)
    args = ap.parse_args()
    tfs, seen = sc.parse_tfs(args.tf), set()
    for kv in filter(None, args.mt5_map.split(",")):
        k, v = kv.split("=")
        sc.MT5_NAMES[k.strip().upper()] = v.strip()
    while True:
        r = run(args, args.symbols, tfs, args.max_age, args.market)
        for e in r["errors"]:
            print("!", e)
        new = [d for d in r["divs"] if d["score"] >= args.min_score and (d["pair"], d["tf"], d["side"], d["level"], d["ts"]) not in seen]
        print(f"\n===== SMT scan {r['clock']} | {'inside a killzone' if r['in_kz'] else 'outside killzones'} =====")
        print("No new divergences." if not new else f"{len(new)} divergence(s):")
        for t in r["triples"]:
            tk = ("T", t["tf"], t["side"], t["level"], t["ts"])
            if t["score"] >= args.min_score and tk not in seen:
                show_triple(t)
                seen.add(tk)
        for d in new:
            show(d)
            seen.add((d["pair"], d["tf"], d["side"], d["level"], d["ts"]))
        if not args.watch:
            break
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
