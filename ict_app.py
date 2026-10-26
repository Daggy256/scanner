#!/usr/bin/env python3
"""
ICT Dashboard v2 (tabbed layout): a local web interface for ict_scanner.py + ict_htf_bias.py + ict_topdown.py + ict_smt.py + ict_priceaction.py

Run   :  python ict_app.py
Opens :  http://127.0.0.1:8765   (your browser opens automatically)
Needs :  pip install pandas numpy yfinance      (nothing else)
Keep ict_app.py, ict_scanner.py, ict_htf_bias.py, ict_topdown.py, ict_smt.py, ict_priceaction.py and ict_news.py in the SAME folder.
Press Ctrl+C in the terminal to stop.

Analysis only. It never places trades. Not financial advice.
"""
import json
import socket
import threading
import time
import webbrowser
from argparse import Namespace
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import pandas as pd

import ict_htf_bias as hb
import ict_news as nw
import ict_priceaction as pa
import ict_scanner as sc
import ict_smt as smt
import ict_topdown as td

TD_CACHE = {}            # top-down result: 60 s (it pulls 1H history + 1-minute data)
TD_TTL = 60
BIAS_CACHE = {}          # HTF bias changes slowly: cache it for 15 minutes
BIAS_TTL = 900


def get_bias(sym, args):
    key = (args.source, sym, args.suffix, args.mt5_offset, args.seed)
    hit = BIAS_CACHE.get(key)
    if hit and time.time() - hit[0] < BIAS_TTL:
        return hit[1]
    b = hb.compute(sym, args)
    BIAS_CACHE[key] = (time.time(), b)
    return b


def run_topdown(q):
    g = lambda k, d="": q.get(k, [d])[0]
    sym = g("symbol", "XAUUSD")
    if sym not in sc.YAHOO:
        raise ValueError(f"unknown symbol {sym}")
    args = Namespace(source=g("source", "yahoo"), suffix=g("suffix"),
                     mt5_offset=float(g("offset", "0") or 0), seed=g("seed", "0"))
    key = (args.source, sym, args.suffix, args.mt5_offset, args.seed)
    hit = TD_CACHE.get(key)
    if hit and time.time() - hit[0] < TD_TTL:
        return hit[1]
    r = td.compute(sym, args)
    TD_CACHE[key] = (time.time(), r)
    return r


PA_CACHE = {}            # price-action result: 30 s
PA_TTL = 30


def run_pa(q):
    g = lambda k, d="": q.get(k, [d])[0]
    args = Namespace(source=g("source", "yahoo"), suffix=g("suffix"),
                     mt5_offset=float(g("offset", "0") or 0), seed=g("seed", "0"))
    mkt = g("mkt", "fx") if g("mkt", "fx") in ("fx", "index", "all") else "fx"
    symbols = {"fx": pa.FX, "index": pa.INDEX}.get(mkt, pa.ALL)
    tfs = sc.parse_tfs(g("tf", "5"))
    htf = g("htf", "1") == "1"
    key = (args.source, args.suffix, args.mt5_offset, args.seed, mkt, tuple(tfs), htf)
    hit = PA_CACHE.get(key)
    if hit and time.time() - hit[0] < PA_TTL:
        return hit[1]
    r = pa.run(args, symbols, tfs, (lambda s: get_bias(s, args)) if htf else None)
    PA_CACHE[key] = (time.time(), r)
    return r


SMT_CACHE = {}           # SMT result: 30 s
SMT_TTL = 30


def run_smt(q):
    g = lambda k, d="": q.get(k, [d])[0]
    args = Namespace(source=g("source", "yahoo"), suffix=g("suffix"),
                     mt5_offset=float(g("offset", "0") or 0), seed=g("seed", "0"))
    mkt = g("mkt", "all") if g("mkt", "all") in ("fx", "index", "all") else "all"
    symbols = {"fx": smt.FX, "index": smt.INDEX}.get(mkt, smt.ALL)
    tfs = sc.parse_tfs(g("tf", "5"))
    max_age = float(g("age", "8") or 8)
    key = (args.source, args.suffix, args.mt5_offset, args.seed, tuple(symbols), tuple(tfs), max_age)
    hit = SMT_CACHE.get(key)
    if hit and time.time() - hit[0] < SMT_TTL:
        return hit[1]
    r = smt.run(args, symbols, tfs, max_age, mkt)
    SMT_CACHE[key] = (time.time(), r)
    return r


def chart_levels(df):
    """Key ICT reference levels drawn on the chart (price, line start time)."""
    days = df.index.normalize().unique()
    if len(days) < 2:
        return []
    today, prev = days[-1], days[-2]
    out = []

    def add(name, cls, price, start):
        out.append(dict(name=name, cls=cls, price=float(price), ts=int(start.timestamp())))

    pdd = df[df.index.normalize() == prev]
    if not pdd.empty:
        add("PDH", "pd", pdd.high.max(), pdd.index[0])
        add("PDL", "pd", pdd.low.min(), pdd.index[0])
    asia = df[(df.index >= today - pd.Timedelta(hours=4)) & (df.index < today)]
    if not asia.empty:
        add("Asia H", "asia", asia.high.max(), asia.index[0])
        add("Asia L", "asia", asia.low.min(), asia.index[0])
    lon = df[(df.index >= today + pd.Timedelta(hours=2)) & (df.index < today + pd.Timedelta(hours=5))]
    if not lon.empty:
        add("London H", "lon", lon.high.max(), lon.index[0])
        add("London L", "lon", lon.low.min(), lon.index[0])
    cur = df[df.index >= today]
    if not cur.empty:
        add("Midnight open", "mo", cur.open.iloc[0], cur.index[0])
    return out


def chart_fvgs(d, keep=8):
    """Unmitigated fair value gaps on the chart timeframe (most recent first kept)."""
    h, l, c = d.high.values, d.low.values, d.close.values
    a = sc.atr(d).iloc[-1]
    out = []
    for i in range(max(2, len(d) - 150), len(d) - 1):
        if l[i] > h[i - 2] and l[i] - h[i - 2] >= 0.1 * a and not (c[i + 1:] < h[i - 2]).any():
            out.append(dict(kind="bull", lo=float(h[i - 2]), hi=float(l[i]), ts=int(d.index[i - 1].timestamp())))
        elif h[i] < l[i - 2] and l[i - 2] - h[i] >= 0.1 * a and not (c[i + 1:] > l[i - 2]).any():
            out.append(dict(kind="bear", lo=float(h[i]), hi=float(l[i - 2]), ts=int(d.index[i - 1].timestamp())))
    return out[-keep:]


def chart_smc(full, since_ts):
    """BOS / CHoCH lines and live order blocks for the chart (raw prices, both directions)."""
    structure, obs = [], []
    atr_now = sc.atr(full).iloc[-1]
    for e in sc.smc_events(full)[-16:]:
        t0, t1 = int(full.index[e["li"]].timestamp()), int(full.index[e["i"]].timestamp())
        if t1 < since_ts:
            continue
        structure.append(dict(kind=e["kind"], dir=e["dir"], level=e["level"], ts0=t0, ts1=t1))
        ob = e["ob"]
        if not ob["broken"] and not ob["touched"] and (ob["hi"] - ob["lo"]) <= 4 * atr_now:   # fresh, sane-sized OBs only
            obs.append(dict(dir=e["dir"], kind=e["kind"], lo=ob["lo"], hi=ob["hi"], touched=ob["touched"],
                            ts=int(full.index[ob["k"]].timestamp())))
    return structure, obs[-5:]


def run_chart(q):
    g = lambda k, d="": q.get(k, [d])[0]
    sym = g("symbol", "XAUUSD")
    if sym not in sc.YAHOO:
        raise ValueError(f"unknown symbol {sym}")
    m = sc.parse_tfs(g("tf", "5"))[0]
    args = Namespace(source=g("source", "yahoo"), suffix=g("suffix"),
                     mt5_offset=float(g("offset", "0") or 0), seed=g("seed", "0"))
    df = sc.fetch(sym, args, m)
    live, full = sc.to_tf(df, m, True), sc.to_tf(df, m)       # live keeps the forming candle
    dig = sc.DIGITS[sym]
    tail = live.iloc[-600:]
    candles = [[int(t.timestamp()), round(float(r.open), dig + 1), round(float(r.high), dig + 1),
                round(float(r.low), dig + 1), round(float(r.close), dig + 1)] for t, r in tail.iterrows()]
    strategy = g("strategy", "ict") if g("strategy", "ict") in ("ict", "smc", "both") else "ict"
    setups = sorted((asdict(s) for s in sc.scan(sym, full, sc.TF_LABEL[m], strategy)), key=lambda s: (-s["score"], -s["rr"]))
    structure, obs = chart_smc(full, candles[0][0]) if strategy in ("smc", "both") else ([], [])
    return dict(structure=structure, obs=obs, strategy=strategy, sym=sym, tf=m, tf_label=sc.TF_LABEL[m], digits=dig, candles=candles, levels=chart_levels(df), fvgs=chart_fvgs(live),
                setups=setups, price=float(live.close.iloc[-1]), updated=time.strftime("%H:%M:%S"))


def run_scan(q):
    g = lambda k, d="": q.get(k, [d])[0]
    args = Namespace(source=g("source", "yahoo"), suffix=g("suffix"),
                     mt5_offset=float(g("offset", "0") or 0), seed=g("seed", "0"))
    symbols = [s for s in g("symbols", ",".join(sc.YAHOO)).split(",") if s in sc.YAHOO]
    use_htf = g("htf", "1") == "1"
    strategy = g("strategy", "ict") if g("strategy", "ict") in ("ict", "smc", "both") else "ict"
    tfs = sc.parse_tfs(g("tf", "5"))
    out = dict(setups=[], bias={}, prices={}, errors=[], digits=sc.DIGITS,
               scanned=time.strftime("%H:%M:%S"))
    for sym in symbols:
        try:
            raw = {}                                           # native data per base timeframe, loaded once per pair
            for m in tfs:
                b = sc.BASE[m]
                if b not in raw:
                    raw[b] = sc.LOADERS[args.source](sym, args, b)
                out["prices"][sym] = float(raw[b].close.iloc[-1])
                out["setups"] += [asdict(s) for s in sc.scan(sym, sc.to_tf(raw[b], m), sc.TF_LABEL[m], strategy)]
        except Exception as e:
            out["errors"].append(f"{sym} scan: {e}")
        if use_htf:
            try:
                out["bias"][sym] = get_bias(sym, args)
            except Exception as e:
                out["errors"].append(f"{sym} HTF bias: {e}")
    try:
        out["news"] = nw.risk(nw.get_events(args.source), symbols)
    except Exception as e:
        out["news"] = {}
        out["errors"].append(f"news calendar: {e}")
    for s in out["setups"]:
        n = out["news"].get(s["symbol"])
        s["news"], s["news_msg"] = (n["status"], n["msg"]) if n else ("", "")
        b = out["bias"].get(s["symbol"])
        if not b:
            s["htf"] = ""
        elif b["dir"] == 0:
            s["htf"] = "neutral"
        elif (b["dir"] > 0) == (s["side"] == "buy"):
            s["htf"] = "aligned"
        else:
            s["htf"] = "counter"
    out["setups"].sort(key=lambda s: (s["htf"] != "aligned", -s["score"], -s["rr"]))
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif u.path == "/api/scan":
            try:
                body = json.dumps(run_scan(parse_qs(u.query)),
                                  default=lambda o: o.item() if hasattr(o, "item") else str(o))
                self._send(200, body, "application/json")
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}), "application/json")
        elif u.path == "/api/chart":
            try:
                body = json.dumps(run_chart(parse_qs(u.query)),
                                  default=lambda o: o.item() if hasattr(o, "item") else str(o))
                self._send(200, body, "application/json")
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}), "application/json")
        elif u.path == "/api/topdown":
            try:
                body = json.dumps(run_topdown(parse_qs(u.query)),
                                  default=lambda o: o.item() if hasattr(o, "item") else str(o))
                self._send(200, body, "application/json")
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}), "application/json")
        elif u.path == "/api/pa":
            try:
                body = json.dumps(run_pa(parse_qs(u.query)),
                                  default=lambda o: o.item() if hasattr(o, "item") else str(o))
                self._send(200, body, "application/json")
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}), "application/json")
        elif u.path == "/api/smt":
            try:
                body = json.dumps(run_smt(parse_qs(u.query)),
                                  default=lambda o: o.item() if hasattr(o, "item") else str(o))
                self._send(200, body, "application/json")
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}), "application/json")
        elif u.path == "/api/news":
            try:
                q = parse_qs(u.query)
                g = lambda k, d="": q.get(k, [d])[0]
                symbols = [s for s in g("symbols", ",".join(sc.YAHOO)).split(",") if s in sc.YAHOO]
                impacts = ("High", "Medium") if g("impact", "high") == "all" else ("High",)
                self._send(200, json.dumps(nw.build(symbols, g("source", "yahoo"), impacts)), "application/json")
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}), "application/json")
        else:
            self._send(404, "not found", "text/plain")


def free_port(start=8765):
    for p in range(start, start + 20):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    return start


PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ICT Dashboard</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#14171c;--mute:#6a7280;--line:#e3e6ec;--accent:#2f6df6;
--buy:#0f9d58;--buyb:#e3f5ea;--sell:#d93025;--sellb:#fce8e6;--warn:#b26a00;--warnb:#fff3d6;--neu:#5b6472;--neub:#eceff3}
@media (prefers-color-scheme:dark){:root{--bg:#0f1216;--card:#171b21;--ink:#e8ebf0;--mute:#98a1af;--line:#272d36;
--accent:#6b9bff;--buy:#4cd18a;--buyb:#12281d;--sell:#ff7b72;--sellb:#321a19;--warn:#f0b24a;--warnb:#33270f;--neu:#a3acb9;--neub:#222831}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between;padding:14px 20px;border-bottom:1px solid var(--line);background:var(--card);position:sticky;top:0;z-index:5}
h1{font-size:18px;margin:0}
.clock{display:flex;gap:10px;align-items:center;font-variant-numeric:tabular-nums}
.chip{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12px;font-weight:600;background:var(--neub);color:var(--neu)}
.chip.buy{background:var(--buyb);color:var(--buy)}.chip.sell{background:var(--sellb);color:var(--sell)}
.chip.warn{background:var(--warnb);color:var(--warn)}.chip.acc{background:var(--accent);color:#fff}
main{max-width:1180px;margin:0 auto;padding:18px 20px 60px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:16px}
.controls{display:flex;flex-wrap:wrap;gap:14px 20px;align-items:end}
label{font-size:12px;color:var(--mute);display:block;margin-bottom:3px}
select,input[type=number],input[type=text]{background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:7px 9px;font:inherit}
.pairs{display:flex;gap:10px;flex-wrap:wrap}.pairs label{display:flex;gap:5px;align-items:center;color:var(--ink);font-size:14px;margin:0}
button{font:inherit;font-weight:600;border:0;border-radius:8px;padding:9px 18px;cursor:pointer;background:var(--accent);color:#fff}
button.sec{background:var(--neub);color:var(--ink)}button:disabled{opacity:.55;cursor:wait}
h2{font-size:15px;margin:22px 0 10px;display:flex;gap:10px;align-items:center}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:12px}
.bias{border:1px solid var(--line);border-radius:12px;background:var(--card);padding:12px 14px}
.bias .top{display:flex;justify-content:space-between;align-items:center}
.sym{font-weight:700;font-size:16px}.price{color:var(--mute);font-variant-numeric:tabular-nums;font-size:13px}
.meter{display:flex;gap:3px;margin:10px 0 6px}.meter i{flex:1;height:7px;border-radius:3px;background:var(--neub)}
.meter i.on.b{background:var(--buy)}.meter i.on.s{background:var(--sell)}
.bias details{font-size:13px;color:var(--mute)}.bias summary{cursor:pointer;color:var(--accent)}
.bias ul{margin:8px 0 0;padding-left:18px}.bias li{margin:3px 0}
.plan{margin-top:8px;font-size:13px;color:var(--ink)}.inv{margin-top:6px;font-size:12.5px;color:var(--warn)}
.setup{border:1px solid var(--line);border-left:4px solid var(--neu);border-radius:12px;background:var(--card);padding:12px 14px;margin-bottom:10px}
.setup.buy{border-left-color:var(--buy)}.setup.sell{border-left-color:var(--sell)}
.setup .row{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center}
.nums{display:flex;gap:18px;flex-wrap:wrap;margin:8px 0;font-variant-numeric:tabular-nums}
.nums div span{display:block;font-size:11px;color:var(--mute);text-transform:uppercase;letter-spacing:.04em}
.nums b{font-size:16px}
.notes{font-size:13px;color:var(--mute)}
.spacer{flex:1}.new{animation:pulse 1.4s ease 3}@keyframes pulse{50%{box-shadow:0 0 0 3px var(--accent)}}
.empty{color:var(--mute);padding:26px;text-align:center;border:1px dashed var(--line);border-radius:12px}
.err{background:var(--sellb);color:var(--sell);padding:8px 12px;border-radius:8px;margin-bottom:8px;font-size:13px}
.foot{color:var(--mute);font-size:12px;margin-top:24px}
.small{font-size:12px;color:var(--mute)}
.nrisk{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px}
.npill{flex:1 1 230px;border:1px solid var(--line);border-left:4px solid var(--buy);border-radius:10px;background:var(--card);padding:8px 12px;font-size:13px}
.npill.CAUTION{border-left-color:var(--warn)}.npill.BLACKOUT{border-left-color:var(--sell)}
.ev{display:grid;grid-template-columns:125px 56px 100px 1fr auto;gap:8px 12px;align-items:center;padding:9px 0;border-bottom:1px solid var(--line);font-size:13.5px}
.ev:last-child{border-bottom:0}.ev.past{opacity:.55}
@media (max-width:760px){.ev{grid-template-columns:1fr 1fr}}
.chbar{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center;margin-bottom:10px}
.chbar label{display:flex;gap:5px;align-items:center;color:var(--ink);font-size:13px;margin:0}
.tabs{display:flex;gap:4px}.tabs button{padding:6px 12px;font-size:13px;background:var(--neub);color:var(--ink)}
.tabs button.on{background:var(--accent);color:#fff}
.chartbox{position:relative;height:500px;user-select:none;touch-action:none;cursor:crosshair}
.chartbox svg{display:block;width:100%;height:100%}
.ctip{position:absolute;left:10px;top:0;font:12px ui-monospace,Consolas,monospace;color:var(--ink);pointer-events:none}
.chleg{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 6px}
.chleg button{padding:5px 10px;font-size:12.5px;background:var(--neub);color:var(--ink);border:1px solid transparent}
.chleg button.on{border-color:var(--accent);background:var(--card)}
.c-up{fill:var(--buy);stroke:var(--buy)}.c-dn{fill:var(--sell);stroke:var(--sell)}
.cgrid{stroke:var(--line);stroke-width:1}.cday{stroke:var(--line);stroke-width:1;stroke-dasharray:2 4}
.ctext{fill:var(--mute);font:11px system-ui,sans-serif}.ctextb{fill:var(--ink);font:600 11.5px system-ui,sans-serif;paint-order:stroke;stroke:var(--card);stroke-width:3px;stroke-linejoin:round}
.k-asia{fill:var(--neu);fill-opacity:.09}.k-lon{fill:var(--accent);fill-opacity:.08}
.k-nya{fill:var(--buy);fill-opacity:.07}.k-nyp{fill:var(--warn);fill-opacity:.08}
.lv{fill:none;stroke-width:1;stroke-dasharray:5 3}
.lv-pd{stroke:#9b6bff}.lv-asia{stroke:var(--accent)}.lv-lon{stroke:var(--warn)}.lv-mo{stroke:var(--neu)}
.lvt{font:10.5px system-ui,sans-serif}
.lvt-pd{fill:#9b6bff}.lvt-asia{fill:var(--accent)}.lvt-lon{fill:var(--warn)}.lvt-mo{fill:var(--neu)}
.fvg-b{fill:var(--buy);fill-opacity:.14;stroke:var(--buy);stroke-opacity:.4}
.fvg-s{fill:var(--sell);fill-opacity:.14;stroke:var(--sell);stroke-opacity:.4}
.risk{fill:var(--sell)}.reward{fill:var(--buy)}.zone{fill:var(--accent);stroke:var(--accent);stroke-dasharray:3 3}
.l-entry{stroke:var(--accent);stroke-width:1.6}.l-sl{stroke:var(--sell);stroke-width:1.4;stroke-dasharray:6 4}
.l-tp{stroke:var(--buy);stroke-width:1.4;stroke-dasharray:6 4}
.mk-buy{fill:var(--buy)}.mk-sell{fill:var(--sell)}
.tag-e{fill:var(--accent)}.tag-sl{fill:var(--sell)}.tag-tp{fill:var(--buy)}.tag-px{fill:var(--ink)}
.tagt{fill:#fff;font:600 11px system-ui,sans-serif}
.cpx{stroke:var(--ink);stroke-width:1;stroke-dasharray:1 3;opacity:.6}
.cross{stroke:var(--mute);stroke-dasharray:2 3;stroke-width:1}
.verdict{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center;margin-bottom:12px}
.verdict b{font-size:17px}
.stp{display:grid;grid-template-columns:34px 1fr;gap:4px 12px;padding:10px 0;border-bottom:1px solid var(--line)}
.stp:last-child{border-bottom:0}
.dot{width:26px;height:26px;border-radius:50%;display:grid;place-items:center;font-weight:700;font-size:13px;background:var(--neub);color:var(--neu)}
.dot.pass{background:var(--buyb);color:var(--buy)}.dot.wait{background:var(--warnb);color:var(--warn)}.dot.fail{background:var(--sellb);color:var(--sell)}
.stp .t{font-size:14px}.stp details{font-size:12.5px;color:var(--mute);margin-top:4px}.stp summary{cursor:pointer;color:var(--accent)}
.stp ul{margin:6px 0 0;padding-left:18px}.stp li{margin:2px 0}
.planbox{margin-top:12px;border:1px solid var(--line);border-left:4px solid var(--buy);border-radius:10px;padding:10px 14px;background:var(--card)}
.chip.smc{background:#f3e5f5;color:#8e24aa}
@media (prefers-color-scheme:dark){.chip.smc{background:#2e1a33;color:#e18cf5}}
.sm{stroke-width:1.3;stroke-dasharray:4 3}.sm-bos{stroke:var(--accent)}.sm-choch{stroke:#d6409f}
.smt{font:600 10.5px system-ui,sans-serif}.smt-bos{fill:var(--accent)}.smt-choch{fill:#d6409f}
.ob-bull{fill:var(--buy);fill-opacity:.16;stroke:var(--buy);stroke-opacity:.55}
.ob-bear{fill:var(--sell);fill-opacity:.16;stroke:var(--sell);stroke-opacity:.55}

/* ---- v2 layout ---- */
header{gap:10px 16px;padding:10px 20px}
.brand{display:flex;align-items:center;gap:8px}.brand h1{font-size:17px}
.nav{display:flex;gap:2px;overflow-x:auto;flex:1 1 auto;scrollbar-width:none;min-width:0}
.nav::-webkit-scrollbar{display:none}
.nav button{background:transparent;color:var(--mute);padding:7px 12px;font-size:14px;border-radius:8px;white-space:nowrap;display:flex;gap:6px;align-items:center}
.nav button:hover{background:var(--neub);color:var(--ink)}
.nav button.on{background:var(--accent);color:#fff}
.bd{font-size:11px;font-weight:700;padding:0 6px;border-radius:999px;background:var(--neub);color:var(--neu);line-height:17px}
.nav button.on .bd{background:rgba(255,255,255,.25);color:#fff}
.hright{display:flex;gap:10px;align-items:center}
button.sm{padding:5px 11px;font-size:13px}
.drawer{background:var(--card);border-bottom:1px solid var(--line);padding:14px 20px}
.drawer[hidden]{display:none}
.drawer .controls{max-width:1180px;margin:0 auto}
.drawer .grp{display:flex;flex-wrap:wrap;gap:12px 18px;align-items:end}
.tab{display:none}.tab.on{display:block}
.thead{display:flex;flex-wrap:wrap;gap:6px 12px;align-items:baseline;margin:2px 0 12px}
.thead h2{margin:0;font-size:17px}
.bar{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center;margin-bottom:12px}
.nrisk{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 14px}
.np{display:flex;gap:7px;align-items:center;padding:4px 11px;border-radius:999px;background:var(--card);border:1px solid var(--line);font-size:12.5px}
.np i{width:8px;height:8px;border-radius:50%;background:var(--buy);flex:none}
.np.CAUTION i{background:var(--warn)}.np.BLACKOUT i{background:var(--sell)}
.np.CAUTION{border-color:var(--warn)}.np.BLACKOUT{border-color:var(--sell)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:18px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:11px 14px;cursor:pointer}
.tile:hover{border-color:var(--accent)}
.tl{font-size:11px;color:var(--mute);text-transform:uppercase;letter-spacing:.05em}
.tv{font-size:20px;font-weight:700;margin:3px 0 1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ts{font-size:12.5px;color:var(--mute)}
.sub{font-size:13px;font-weight:600;color:var(--mute);text-transform:uppercase;letter-spacing:.05em;margin:20px 0 10px}
.bias .price{margin-top:2px}
.bias .chips{display:flex;gap:6px;flex-wrap:wrap;margin:6px 0 2px}
.bias details{margin-top:8px}
.ssym{font-size:16px}.mname{font-weight:600}
.setup{padding:11px 14px}
.setup .nums{gap:22px;margin:8px 0 6px}
.setup .nums b{font-size:15px}
details.why summary{cursor:pointer;color:var(--accent);font-size:13px}
details.why .notes{margin-top:6px}
.layers{position:relative}
.layers summary{list-style:none;cursor:pointer;background:var(--neub);padding:6px 12px;border-radius:8px;font-size:13px;font-weight:600}
.layers summary::-webkit-details-marker{display:none}
.layers .pop{position:absolute;z-index:6;top:36px;left:0;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;display:grid;gap:7px;min-width:190px;box-shadow:0 6px 20px rgba(0,0,0,.15)}
.layers .pop label{display:flex;gap:7px;align-items:center;font-size:13px;color:var(--ink);margin:0}
.layers .pop select{width:100%}
.heads{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
@media (max-width:760px){header{padding:8px 12px}main{padding:14px 12px 50px}.hright .small{display:none}}
</style></head><body>
<header>
  <div class="brand"><h1>ICT Dashboard</h1></div>
  <nav class="nav" id="nav"></nav>
  <div class="hright"><span class="small">NY</span><b id="nyclock">--:--:--</b><span id="kz" class="chip">--</span>
    <button id="go" class="sm">Scan now</button><button class="sec sm" id="gear" title="Data source, pairs, timeframe, refresh">Settings</button></div>
</header>
<div class="drawer" id="drawer" hidden><div class="controls">
  <div class="grp">
    <div><label>Data source</label>
      <select id="source"><option value="yahoo">Yahoo (free)</option><option value="mt5">MetaTrader 5</option><option value="demo">Demo (fake data)</option></select></div>
    <div class="mt5only" style="display:none"><label>MT5 offset (hrs vs UTC)</label><input type="number" id="offset" value="0" step="0.5" style="width:90px"></div>
    <div class="mt5only" style="display:none"><label>Symbol suffix</label><input type="text" id="suffix" placeholder="e.g. m" style="width:80px"></div>
    <div><label>Pairs</label><div class="pairs" id="pairs"></div></div>
    <div><label>Scan timeframe</label>
      <select id="tf"><option value="1">1 min</option><option value="5" selected>5 min</option><option value="15">15 min</option><option value="60">1 hour</option><option value="both">5m + 15m</option><option value="all">All (1m 5m 15m 1h)</option></select></div>
    <div><label>Strategy</label>
      <select id="strat"><option value="ict" selected>ICT models</option><option value="smc">SMC (BOS / CHoCH / OB)</option><option value="both">Both</option></select></div>
    <div><label>Auto-refresh</label>
      <select id="auto"><option value="0">Off</option><option value="60">1 min</option><option value="300" selected>5 min</option><option value="900">15 min</option></select></div>
    <label style="display:flex;gap:6px;align-items:center;margin:0 0 6px"><input type="checkbox" id="htf" checked> HTF bias</label>
    <label style="display:flex;gap:6px;align-items:center;margin:0 0 6px"><input type="checkbox" id="snd"> Sound on new setup</label>
  </div>
</div></div>
<main>
<div class="nrisk" id="nrisk"></div>
<div id="errs"></div>

<section class="tab" id="tab-overview">
  <div class="tiles">
    <div class="tile" data-go="topdown"><div class="tl">Top-down</div><div class="tv" id="ov-td-v">...</div><div class="ts" id="ov-td-s">&nbsp;</div></div>
    <div class="tile" data-go="setups"><div class="tl">Trade setups</div><div class="tv" id="ov-st-v">...</div><div class="ts" id="ov-st-s">&nbsp;</div></div>
    <div class="tile" data-go="pa"><div class="tl">Price action</div><div class="tv" id="ov-pa-v">...</div><div class="ts" id="ov-pa-s">&nbsp;</div></div>
    <div class="tile" data-go="smt"><div class="tl">SMT divergence</div><div class="tv" id="ov-smt-v">...</div><div class="ts" id="ov-smt-s">&nbsp;</div></div>
    <div class="tile" data-go="news"><div class="tl">Next high-impact news</div><div class="tv" id="ov-news-v">...</div><div class="ts" id="ov-news-s">&nbsp;</div></div>
  </div>
  <div class="thead"><h2>Higher-timeframe bias</h2><span class="small" id="bnote"></span></div>
  <div class="grid" id="bias"><div class="empty" style="grid-column:1/-1">Loading...</div></div>
  <div class="sub">Best setups right now</div>
  <div id="ovsetups"></div>
</section>

<section class="tab" id="tab-chart">
  <div class="thead"><h2>Live chart</h2><span class="small" id="cnote"></span></div>
  <div class="panel">
    <div class="bar">
      <div class="tabs" id="ctabs"></div>
      <select id="ctf"><option value="1">1 min</option><option value="5">5 min</option><option value="15">15 min</option><option value="60">1 hour</option></select>
      <details class="layers"><summary>Layers</summary><div class="pop">
        <label><input type="checkbox" data-sh="st" checked> Setups</label>
        <label><input type="checkbox" data-sh="lv" checked> Levels</label>
        <label><input type="checkbox" data-sh="fvg" checked> FVGs</label>
        <label><input type="checkbox" data-sh="kz" checked> Killzones</label>
        <label><input type="checkbox" data-sh="sm" checked> BOS / CHoCH</label>
        <label><input type="checkbox" data-sh="ob" checked> Order blocks</label>
        <select id="cref"><option value="0">Refresh: off</option><option value="15">Refresh: 15 s</option><option value="30" selected>Refresh: 30 s</option><option value="60">Refresh: 60 s</option></select>
      </div></details>
      <span class="spacer"></span>
      <button class="sec sm" id="zout">&minus;</button><button class="sec sm" id="zin">+</button><button class="sec sm" id="zlat">Latest</button>
    </div>
    <div id="chartbox" class="chartbox"><div class="empty">Loading chart...</div></div>
    <div class="chleg" id="clegend"></div>
    <div class="small">Scroll to zoom, drag to pan, double-click to reset. Green box = reward, red = risk, blue band = entry zone. Times are New York.</div>
  </div>
</section>

<section class="tab" id="tab-setups">
  <div class="thead"><h2>Trade setups</h2><span class="small" id="snote"></span></div>
  <div class="bar">
    <select id="flt"><option value="all">All setups</option><option value="aligned">HTF-aligned only</option></select>
    <label class="small" style="display:flex;gap:6px;align-items:center;margin:0">Show top <input type="number" id="top" value="10" min="1" max="50" style="width:64px"></label>
    <span class="spacer"></span><button class="sec sm" id="csv">Download CSV</button>
  </div>
  <div id="setups"></div>
</section>

<section class="tab" id="tab-topdown">
  <div class="thead"><h2>Top-down workflow</h2><span class="small" id="tdnote"></span></div>
  <div class="panel">
    <div class="bar"><div class="tabs" id="tdtabs"></div><span class="spacer"></span><button class="sec sm" id="tdrun">Run top-down</button></div>
    <div id="tdbox"><div class="empty">Pick a pair and press "Run top-down": news gate, then Daily bias, 4H POI, 1H confirmation, 1M trigger.</div></div>
  </div>
</section>

<section class="tab" id="tab-pa">
  <div class="thead"><h2>Price action</h2><span class="small" id="panote"></span></div>
  <div class="bar">
    <select id="pamkt"><option value="fx" selected>FX / gold</option><option value="index">Indices (NQ ES YM)</option><option value="all">All markets</option></select>
    <select id="patf"><option value="1">1 min</option><option value="5" selected>5 min</option><option value="15">15 min</option><option value="60">1 hour</option><option value="5,15,60">5m + 15m + 1h</option></select>
    <select id="pamin"><option value="0">All scores</option><option value="2" selected>Score 2+</option><option value="3">Score 3+</option><option value="4">Score 4+</option></select>
    <label class="small" style="display:flex;gap:6px;align-items:center;margin:0"><input type="checkbox" id="paread" checked> Market read</label>
    <span class="spacer"></span><button class="sec sm" id="parun">Scan price action</button>
  </div>
  <div id="pabox"><div class="empty">Zone reaction, false breakout, break &amp; retest, with-trend pullback and inside-bar breakout. Press "Scan price action".</div></div>
</section>

<section class="tab" id="tab-smt">
  <div class="thead"><h2>SMT divergence</h2><span class="small" id="smtnote"></span></div>
  <div class="bar">
    <select id="smmkt"><option value="all" selected>FX + indices</option><option value="fx">FX / gold</option><option value="index">Indices (NQ ES YM)</option></select>
    <select id="smtf"><option value="1">1 min</option><option value="5" selected>5 min</option><option value="15">15 min</option><option value="60">1 hour</option><option value="1,5">1m + 5m</option><option value="all">All timeframes</option></select>
    <select id="smage"><option value="3">Last 3 hours</option><option value="8" selected>Last 8 hours</option><option value="24">Last 24 hours</option></select>
    <select id="smmin"><option value="1">All scores</option><option value="2" selected>Score 2+</option><option value="3">Score 3+</option></select>
    <span class="spacer"></span><button class="sec sm" id="smrun">Scan SMT</button>
  </div>
  <div id="smtbox"><div class="empty">One market takes a level, its correlated partner refuses to. Use it to confirm a sweep, never as an entry on its own.</div></div>
</section>

<section class="tab" id="tab-news">
  <div class="thead"><h2>News &amp; calendar</h2><span class="small" id="nnote"></span><span class="spacer"></span>
    <select id="nimp"><option value="high">High impact only</option><option value="all">High + medium</option></select></div>
  <div class="panel" id="events"><div class="small">Loading...</div></div>
  <div class="sub">Headlines <span style="text-transform:none;letter-spacing:0;font-weight:400">(rough keyword tone, not a trading signal)</span></div>
  <div class="heads" id="heads"></div>
</section>
<p class="foot">Analysis only. This tool never places trades. Verify every setup on your own chart, avoid CPI / NFP / FOMC, trade demo first. Not financial advice.</p>
</main>
<script>
const $=s=>document.querySelector(s), PAIRS=["XAUUSD","USDJPY","EURUSD","GBPUSD"];
let seen=new Set(), first=true, timer=null, data=null, busy=false;
$("#pairs").innerHTML=PAIRS.map(p=>`<label><input type="checkbox" value="${p}" checked>${p}</label>`).join("");
const esc=s=>String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const fmt=(sym,x)=>Number(x).toFixed((data&&data.digits[sym])||5);

function clock(){
  const d=new Date(), p=new Intl.DateTimeFormat("en-GB",{timeZone:"America/New_York",hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false,weekday:"short"}).formatToParts(d);
  const g=t=>p.find(x=>x.type===t).value, h=+g("hour"), m=+g("minute"), wd=g("weekday");
  $("#nyclock").textContent=`${wd} ${g("hour")}:${g("minute")}:${g("second")}`;
  let t="Outside killzone",c="chip";
  if(wd==="Sat"||(wd==="Sun"&&h<17)||(wd==="Fri"&&h>=17)){t="Market closed";c="chip"}
  else if(h>=2&&h<5){t=h===3?"London killzone + Silver Bullet":"London killzone";c="chip buy"}
  else if(h>=7&&h<11){t=h===10?"NY AM killzone + Silver Bullet":"NY AM killzone";c="chip buy"}
  else if(h>=13&&h<16){t=h===14?"NY PM killzone + Silver Bullet":"NY PM killzone";c="chip buy"}
  $("#kz").className=c;$("#kz").textContent=t;tickCd();
}
setInterval(clock,1000);clock();

$("#source").onchange=()=>document.querySelectorAll(".mt5only").forEach(e=>e.style.display=$("#source").value==="mt5"?"block":"none");

function query(){
  const syms=[...document.querySelectorAll("#pairs input:checked")].map(i=>i.value).join(",");
  return new URLSearchParams({source:$("#source").value,symbols:syms,htf:$("#htf").checked?1:0,tf:$("#tf").value,strategy:$("#strat").value,offset:$("#offset").value||0,suffix:$("#suffix").value||""}).toString();
}
async function scan(){
  if(busy)return; busy=true; $("#go").disabled=true; $("#go").textContent="Scanning...";
  try{
    const r=await fetch("/api/scan?"+query()); data=await r.json();
    if(data.error){$("#errs").innerHTML=`<div class="err">${esc(data.error)}</div>`}
    else render();
  }catch(e){$("#errs").innerHTML=`<div class="err">Could not reach the local server: ${esc(e)}</div>`}
  busy=false; $("#go").disabled=false; $("#go").textContent="Scan now";
}
function setupCard(s,ix,isNew){
  const tag=s.htf==="aligned"?`<span class="chip buy">Aligned HTF</span>`:s.htf==="counter"?`<span class="chip warn">Counter-trend</span>`:s.htf==="neutral"?`<span class="chip">HTF neutral</span>`:"";
  const nwc=s.news==="BLACKOUT"?`<span class="chip sell">News blackout</span>`:s.news==="CAUTION"?`<span class="chip warn">News caution</span>`:"";
  const nwm=s.news&&s.news!=="CLEAR"?`<div class="notes" style="color:var(--warn);margin-bottom:6px">${esc(s.news_msg)}</div>`:"";
  const txt=`${s.symbol} ${s.tf} ${s.side.toUpperCase()} LIMIT ${fmt(s.symbol,s.entry)} SL ${fmt(s.symbol,s.sl)} TP ${fmt(s.symbol,s.tp)} (${s.model}, RR ${s.rr})`;
  return `<div class="setup ${s.side} ${isNew?"new":""}"><div class="row">
    <b class="ssym">${s.symbol}</b><span class="chip ${s.side}">${s.side.toUpperCase()} LIMIT</span><span class="chip acc">${s.tf}</span>${s.strat==="SMC"?'<span class="chip smc">SMC</span>':""}
    <span class="mname">${esc(s.model)}</span>${tag}${nwc}${isNew?'<span class="chip acc">NEW</span>':""}
    <span class="spacer"></span><span class="chip">${s.status}</span><span class="chip">Score ${s.score}/4</span></div>
    <div class="nums"><div><span>Entry</span><b>${fmt(s.symbol,s.entry)}</b></div><div><span>Stop</span><b>${fmt(s.symbol,s.sl)}</b></div>
    <div><span>Target</span><b>${fmt(s.symbol,s.tp)}</b></div><div><span>R:R</span><b>${s.rr}</b></div></div>
    ${nwm}<div class="row"><details class="why"><summary>Why this setup</summary><div class="notes">${esc(s.notes)} <span class="small">(formed ${s.formed} NY)</span></div></details>
    <span class="spacer"></span><button class="sec sm" onclick="showOnChart(${ix})">Show on chart</button><button class="sec sm" data-copy="${esc(txt)}">Copy</button></div></div>`}
function render(){
  $("#errs").innerHTML=(data.errors||[]).map(e=>`<div class="err">${esc(e)}</div>`).join("");
  const bs=Object.entries(data.bias||{});
  $("#bnote").textContent=bs.length?"(cached up to 15 min)":"";
  $("#bias").innerHTML=bs.length?bs.map(([sym,b])=>{
    const cls=b.dir>0?"buy":b.dir<0?"sell":"", n=Math.abs(b.score);
    const meter=[1,2,3,4,5].map(i=>`<i class="${i<=n?"on "+(b.dir>0?"b":"s"):""}"></i>`).join("");
    const zone=b.zone?`<span class="chip">${esc(b.zone)} ${Math.round(b.pct*100)}%</span>`:"";
    const dr=(b.up?`<span class="chip">&uarr; ${esc(b.up[0])}</span>`:"")+(b.dn?`<span class="chip">&darr; ${esc(b.dn[0])}</span>`:"");
    return `<div class="bias"><div class="top"><span class="sym">${sym}</span><span class="chip ${cls}">${b.label}</span></div>
    <div class="price">${fmt(sym,b.price)} &middot; ${b.conf} (${b.score>0?"+":""}${b.score}/5)</div>
    <div class="meter">${meter}</div><div class="chips">${zone}${dr}</div>
    <details><summary>Plan &amp; reasoning</summary><div class="plan">${esc(b.plan)}</div>${b.inv?`<div class="inv">${esc(b.inv)}</div>`:""}
    <ul>${b.lines.map(l=>`<li>${esc(l)}</li>`).join("")}</ul></details></div>`}).join("")
    :`<div class="empty" style="grid-column:1/-1">HTF bias is off or unavailable.</div>`;

  let list=data.setups.slice();
  if($("#flt").value==="aligned") list=list.filter(s=>s.htf==="aligned");
  list=list.slice(0,+$("#top").value||10);
  const px=Object.entries(data.prices||{}).map(([k,v])=>`${k} ${fmt(k,v)}`).join("  |  ");
  $("#snote").textContent=`${list.length} of ${data.setups.length} shown | scanned ${data.scanned} | ${px}`;
  let anyNew=false;
  LIST=list;
  const cards=list.map((s,ix)=>{
    const key=[s.symbol,s.tf,s.model,s.side,Number(s.entry).toFixed(2)].join("|");
    const isNew=!first&&!seen.has(key); if(isNew)anyNew=true; seen.add(key);
    return setupCard(s,ix,isNew)});
  const none=`<div class="empty">No qualifying setups right now. This is normal: the filters are strict and many hours have none.</div>`;
  $("#setups").innerHTML=cards.length?cards.join(""):none;
  $("#ovsetups").innerHTML=cards.length?cards.slice(0,3).join(""):none;
  badge("setups",data.setups.length);
  const al=data.setups.filter(s=>s.htf==="aligned").length;
  ovset("st",data.setups.length,`${al} aligned with HTF`);
  if(anyNew&&$("#snd").checked)beep();
  first=false;
  loadNews();
}
function beep(){try{const a=new AudioContext(),o=a.createOscillator(),g=a.createGain();o.connect(g);g.connect(a.destination);o.frequency.value=880;g.gain.value=.15;o.start();o.stop(a.currentTime+.25)}catch(e){}}
/* ---------------- live chart ---------------- */
let LIST=[];
const CH={sym:"XAUUSD",tf:5,d:null,N:120,off:0,sel:null,timer:null,drag:null,g:null,show:{lv:true,fvg:true,kz:true,st:true,sm:true,ob:true}};
const NYP=new Intl.DateTimeFormat("en-GB",{timeZone:"America/New_York",hourCycle:"h23",hour:"2-digit",minute:"2-digit",day:"2-digit",month:"short",weekday:"short"});
const _np=new Map();
function nyParts(ts){let r=_np.get(ts);if(!r){const p=NYP.formatToParts(new Date(ts*1000)),g=t=>p.find(x=>x.type===t).value;
  r={h:(+g("hour"))%24,m:g("minute"),hh:g("hour"),day:g("day"),mon:g("month"),wd:g("weekday")};if(_np.size>6000)_np.clear();_np.set(ts,r)}return r}
const skey=s=>s.model+"|"+s.side+"|"+Math.round(s.entry*1e5);
function lowerBound(a,v){let lo=0,hi=a.length;while(lo<hi){const m=(lo+hi)>>1;if(a[m]<v)lo=m+1;else hi=m}return lo}
function niceStep(range,target){const raw=range/target,mag=Math.pow(10,Math.floor(Math.log10(raw))),f=raw/mag;return(f<1.5?1:f<3?2:f<7?5:10)*mag}
const r1=x=>x.toFixed(1);
const tfNum=s=>/h$/i.test(String(s))?parseInt(s)*60:parseInt(s), tfLab=m=>m>=60?(m/60)+"h":m+"m";

function chartSVG(d,st,W,H){
  const cs=d.candles,n=cs.length;if(!n)return null;
  const ts=d.ts,dig=d.digits,f=x=>Number(x).toFixed(dig);
  const M={l:8,r:82,t:16,b:26},PAD=14;
  const N=Math.max(20,Math.min(st.N,n)),off=Math.max(0,Math.min(st.off,n-N));
  const i1=n-off,i0=Math.max(0,i1-N),S=N+PAD;
  const pw=W-M.l-M.r,ph=H-M.t-M.b,step=pw/S,xR=M.l+pw;
  const X=i=>M.l+(i-i0+.5)*step,idxOf=t=>lowerBound(ts,t);
  let lo=Infinity,hi=-Infinity;
  for(let i=i0;i<i1;i++){if(cs[i][3]<lo)lo=cs[i][3];if(cs[i][2]>hi)hi=cs[i][2]}
  const sts=st.show.st?(d.setups||[]).filter(s=>idxOf(s.ts)<i1):[];
  for(const s of sts)for(const p of[s.entry,s.sl,s.tp]){if(p<lo)lo=p;if(p>hi)hi=p}
  const r0=(hi-lo)||1;
  const lvls=st.show.lv?(d.levels||[]).filter(l=>idxOf(l.ts)<i1&&l.price>lo-.35*r0&&l.price<hi+.35*r0):[];
  for(const l of lvls){if(l.price<lo)lo=l.price;if(l.price>hi)hi=l.price}
  const pad=((hi-lo)*.05)||1,ymin=lo-pad,ymax=hi+pad,Y=p=>M.t+(ymax-p)/(ymax-ymin)*ph;
  const o=[`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">`];

  if(st.show.kz){                                   // ICT sessions / killzones (NY time)
    const KZ={asia:"ASIA",lon:"LONDON KZ",nya:"NY AM KZ",nyp:"NY PM KZ"};
    const cat=h=>h>=20?"asia":h>=2&&h<5?"lon":h>=7&&h<11?"nya":h>=13&&h<16?"nyp":null;
    let run=null;
    const emit=(r,e)=>{if(!r||!r.k)return;const x=X(r.s)-step/2,w=X(e)-X(r.s)+step;
      o.push(`<rect class="k-${r.k}" x="${r1(x)}" y="${M.t}" width="${r1(w)}" height="${r1(ph)}"/>`);
      if(w>50)o.push(`<text class="ctext" x="${r1(x+3)}" y="${M.t+10}">${KZ[r.k]}</text>`)};
    for(let i=i0;i<i1;i++){const k=cat(nyParts(cs[i][0]).h);if(!run||run.k!==k){emit(run,i-1);run={k,s:i}}}
    emit(run,i1-1);
  }
  const gs=niceStep(ymax-ymin,6);                   // price grid + axis
  for(let p=Math.ceil(ymin/gs)*gs;p<ymax;p+=gs){const pp=Math.round(p/gs)*gs,y=Y(pp);
    o.push(`<line class="cgrid" x1="${M.l}" x2="${r1(xR)}" y1="${r1(y)}" y2="${r1(y)}"/><text class="ctext" x="${r1(xR+6)}" y="${r1(y+4)}">${f(pp)}</text>`)}
  const kk=Math.max(1,Math.ceil(80/step));let prevDay=null;   // time axis + day separators
  for(let i=i0;i<i1;i++){const p=nyParts(cs[i][0]);
    if(prevDay!==null&&p.day!==prevDay){const x=X(i)-step/2;
      o.push(`<line class="cday" x1="${r1(x)}" x2="${r1(x)}" y1="${M.t}" y2="${r1(M.t+ph)}"/><text class="ctext" x="${r1(x+3)}" y="${r1(M.t+ph-4)}">${p.wd} ${p.day} ${p.mon}</text>`)}
    prevDay=p.day;
    if(i%kk===0)o.push(`<text class="ctext" text-anchor="middle" x="${r1(X(i))}" y="${H-8}">${p.hh}:${p.m}</text>`)}
  if(st.show.fvg)for(const g of d.fvgs||[]){        // unmitigated FVGs
    const ix=idxOf(g.ts);if(ix>=i1||g.hi<ymin||g.lo>ymax)continue;
    const x0=ix>=i0?X(ix):M.l,y1=Y(Math.min(g.hi,ymax)),y2=Y(Math.max(g.lo,ymin));
    o.push(`<rect class="${g.kind==="bull"?"fvg-b":"fvg-s"}" x="${r1(x0)}" y="${r1(y1)}" width="${r1(Math.max(0,xR-x0))}" height="${r1(Math.max(1,y2-y1))}"/><text class="ctext" x="${r1(x0+3)}" y="${r1(y1+10)}">FVG</text>`)}
  if(st.show.ob)for(const b of d.obs||[]){          // SMC order blocks (not yet broken through)
    const ix=idxOf(b.ts);if(ix>=i1||b.hi<ymin||b.lo>ymax)continue;
    const x0=ix>=i0?X(ix)-step/2:M.l,y1=Y(Math.min(b.hi,ymax)),y2=Y(Math.max(b.lo,ymin));
    o.push(`<rect class="${b.dir==="bull"?"ob-bull":"ob-bear"}" x="${r1(x0)}" y="${r1(y1)}" width="${r1(Math.max(0,xR-x0))}" height="${r1(Math.max(1.5,y2-y1))}"/><text class="ctext" x="${r1(Math.max(x0,M.l)+3)}" y="${r1(y1+10)}">${b.dir==="bull"?"Bull":"Bear"} OB${b.touched?" (tested)":""}</text>`)}
  if(st.show.sm)for(const e of d.structure||[]){    // BOS / CHoCH: broken swing level, drawn from the swing to the break
    const a0=idxOf(e.ts0),a1=idxOf(e.ts1);if(a1<i0||a0>=i1)continue;
    const x0=a0>=i0?X(a0):M.l,x1=X(Math.min(a1,i1-1)),y=Y(e.level);if(y<M.t||y>M.t+ph)continue;
    const k=e.kind==="BOS"?"bos":"choch";
    o.push(`<line class="sm sm-${k}" x1="${r1(x0)}" x2="${r1(x1)}" y1="${r1(y)}" y2="${r1(y)}"/><text class="smt smt-${k}" text-anchor="middle" x="${r1(Math.max(M.l+20,(x0+x1)/2))}" y="${r1(e.dir==="bull"?y-4:y+12)}">${e.kind}</text>`)}
  const usedY=[];
  for(const l of lvls){                             // key levels (labels are nudged apart when prices are close)
    const ix=idxOf(l.ts),x0=ix>=i0?X(ix):M.l,y=Y(l.price);let ty=y-3;
    while(usedY.some(u=>Math.abs(u-ty)<11))ty-=11;usedY.push(ty);
    o.push(`<line class="lv lv-${l.cls}" x1="${r1(x0)}" x2="${r1(xR)}" y1="${r1(y)}" y2="${r1(y)}"/><text class="lvt lvt-${l.cls}" text-anchor="end" x="${r1(xR-4)}" y="${r1(ty)}">${l.name} ${f(l.price)}</text>`)}
  const bw=Math.max(1,step*.68);                    // candles
  for(let i=i0;i<i1;i++){const c=cs[i],x=X(i),up=c[4]>=c[1],yo=Y(c[1]),yc=Y(c[4]);
    o.push(`<line class="${up?"c-up":"c-dn"}" x1="${r1(x)}" x2="${r1(x)}" y1="${r1(Y(c[2]))}" y2="${r1(Y(c[3]))}"/><rect class="${up?"c-up":"c-dn"}" x="${r1(x-bw/2)}" y="${r1(Math.min(yo,yc))}" width="${r1(bw)}" height="${r1(Math.max(1,Math.abs(yc-yo)))}"/>`)}

  const tags=[];                                    // setups: zone, risk/reward boxes, lines, markers
  const many=sts.length>3;
  sts.slice().sort((a,b)=>(skey(a)===st.sel)-(skey(b)===st.sel)).forEach(s=>{
    const ix=Math.max(0,idxOf(s.ts)),sel=skey(s)===st.sel,x0=ix>=i0?X(ix):M.l,w=Math.max(0,xR-x0);
    const ye=Y(s.entry),ys=Y(s.sl),yt=Y(s.tp);
    o.push(`<g opacity="${sel?1:(many?.35:.55)}">`);
    if(s.zhi>s.zlo)o.push(`<rect class="zone" x="${r1(x0)}" y="${r1(Y(s.zhi))}" width="${r1(w)}" height="${r1(Math.max(1.5,Y(s.zlo)-Y(s.zhi)))}" fill-opacity=".16" stroke-opacity=".6"/>`);
    o.push(`<rect class="risk" x="${r1(x0)}" y="${r1(Math.min(ye,ys))}" width="${r1(w)}" height="${r1(Math.abs(ys-ye))}" fill-opacity="${sel?.2:.1}"/>`);
    o.push(`<rect class="reward" x="${r1(x0)}" y="${r1(Math.min(ye,yt))}" width="${r1(w)}" height="${r1(Math.abs(yt-ye))}" fill-opacity="${sel?.2:.1}"/>`);
    o.push(`<line class="l-entry" x1="${r1(x0)}" x2="${r1(xR)}" y1="${r1(ye)}" y2="${r1(ye)}"/><line class="l-sl" x1="${r1(x0)}" x2="${r1(xR)}" y1="${r1(ys)}" y2="${r1(ys)}"/><line class="l-tp" x1="${r1(x0)}" x2="${r1(xR)}" y1="${r1(yt)}" y2="${r1(yt)}"/>`);
    const lbl=`${s.side.toUpperCase()} ${s.model} | ${s.rr}R | ${s.status}`,fits=(xR-x0)>lbl.length*6.4+10;
    o.push(`<text class="${sel?"ctextb":"ctext"}" ${fits?`x="${r1(x0+4)}"`:`text-anchor="end" x="${r1(x0-6)}"`} y="${r1(ye+(s.side==="buy"?13:-5))}">${esc(lbl)}</text>`);
    if(ix>=i0&&ix<i1){const x=X(ix),c=cs[ix];
      if(s.side==="buy"){const y=Y(c[3])+4;o.push(`<polygon class="mk-buy" points="${r1(x)},${r1(y)} ${r1(x-5)},${r1(y+9)} ${r1(x+5)},${r1(y+9)}"/>`)}
      else{const y=Y(c[2])-4;o.push(`<polygon class="mk-sell" points="${r1(x)},${r1(y)} ${r1(x-5)},${r1(y-9)} ${r1(x+5)},${r1(y-9)}"/>`)}}
    o.push("</g>");
    if(sel)tags.push([ye,"E "+f(s.entry),"tag-e"],[ys,"SL "+f(s.sl),"tag-sl"],[yt,"TP "+f(s.tp),"tag-tp"]);
  });
  const last=cs[n-1][4],yl=Y(last);                 // current price
  o.push(`<line class="cpx" x1="${M.l}" x2="${r1(xR)}" y1="${r1(yl)}" y2="${r1(yl)}"/>`);
  tags.push([yl,f(last),"tag-px"]);
  for(const [y,t,c] of tags)o.push(`<rect class="${c}" x="${r1(xR+1)}" y="${r1(y-9)}" width="${M.r-3}" height="18" rx="3"/><text class="tagt" x="${r1(xR+6)}" y="${r1(y+4)}">${t}</text>`);
  o.push(`<line id="cx" class="cross" x1="0" x2="0" y1="${M.t}" y2="${r1(M.t+ph)}" opacity="0"/></svg>`);
  return{svg:o.join(""),g:{i0,i1,step,Ml:M.l,W,H}};
}
function drawChart(){
  const box=$("#chartbox"),d=CH.d;
  if(!d){box.innerHTML='<div class="empty">Loading chart...</div>';return}
  const r=chartSVG(d,CH,box.clientWidth||900,box.clientHeight||500);
  if(!r){box.innerHTML='<div class="empty">No candles returned for this pair.</div>';return}
  CH.g=r.g;box.innerHTML=r.svg+'<div id="ctip" class="ctip"></div>';
}
function renderLegend(){
  const d=CH.d;if(!d)return;
  $("#clegend").innerHTML=d.setups.length?d.setups.map((s,i)=>`<button class="${skey(s)===CH.sel?"on":""}" onclick="pickSetup(${i})">${s.side.toUpperCase()} ${esc(s.model)} &middot; ${s.rr}R &middot; ${s.status}</button>`).join(""):'<span class="small">No setups on this pair / timeframe right now.</span>';
}
function pickSetup(i){const s=CH.d&&CH.d.setups[i];if(!s)return;CH.sel=skey(s);drawChart();renderLegend()}
function syncTabs(){document.querySelectorAll("#ctabs button").forEach(b=>b.classList.toggle("on",b.dataset.sym===CH.sym))}
async function loadChart(){
  const q=new URLSearchParams({source:$("#source").value,symbol:CH.sym,tf:CH.tf,strategy:$("#strat").value,offset:$("#offset").value||0,suffix:$("#suffix").value||""});
  try{
    const r=await fetch("/api/chart?"+q),j=await r.json();
    if(j.error){$("#cnote").textContent="chart error: "+j.error;return}
    j.ts=j.candles.map(c=>c[0]);
    if(!CH.sel||!j.setups.some(s=>skey(s)===CH.sel))CH.sel=j.setups.length?skey(j.setups[0]):null;
    CH.d=j;drawChart();renderLegend();
    $("#cnote").textContent=`${j.sym} ${tfLab(j.tf)} | last ${Number(j.price).toFixed(j.digits)} | updated ${j.updated}`;
  }catch(e){$("#cnote").textContent="chart unavailable: "+e}
}
function showOnChart(i){const s=LIST[i];if(!s)return;CH.sym=s.symbol;CH.tf=tfNum(s.tf);CH.sel=skey(s);CH.off=0;
  $("#ctf").value=String(CH.tf);syncTabs();loadChart();go("chart")}
function armChart(){clearInterval(CH.timer);const s=+$("#cref").value;if(s)CH.timer=setInterval(loadChart,s*1000)}
(function initChart(){
  const box=$("#chartbox");let raf=0;
  const redraw=()=>{if(!raf)raf=requestAnimationFrame(()=>{raf=0;drawChart()})};
  $("#ctabs").innerHTML=PAIRS.map(p=>`<button data-sym="${p}">${p}</button>`).join("");
  document.querySelectorAll("#ctabs button").forEach(b=>b.onclick=()=>{CH.sym=b.dataset.sym;CH.off=0;CH.sel=null;syncTabs();loadChart()});
  CH.tf=["1","5","15","60"].includes($("#tf").value)?+$("#tf").value:5;$("#ctf").value=String(CH.tf);syncTabs();
  $("#ctf").onchange=()=>{CH.tf=+$("#ctf").value;CH.sel=null;loadChart()};
  $("#cref").onchange=armChart;
  document.querySelectorAll("[data-sh]").forEach(c=>c.onchange=()=>{CH.show[c.dataset.sh]=c.checked;drawChart()});
  $("#zin").onclick=()=>{CH.N=Math.max(20,Math.round(CH.N*.8));drawChart()};
  $("#zout").onclick=()=>{CH.N=Math.min(300,Math.round(CH.N*1.25));drawChart()};
  $("#zlat").onclick=()=>{CH.off=0;CH.N=120;drawChart()};
  $("#source").addEventListener("change",()=>{CH.d=null;CH.sel=null;loadChart()});
  $("#strat").addEventListener("change",()=>{CH.d=null;CH.sel=null;loadChart()});
  box.addEventListener("pointerdown",e=>{CH.drag={x:e.clientX,off:CH.off};if(box.setPointerCapture)box.setPointerCapture(e.pointerId)});
  box.addEventListener("pointermove",e=>{
    const g=CH.g,d=CH.d;if(!g||!d)return;
    if(CH.drag){const nb=Math.round((e.clientX-CH.drag.x)/g.step),mx=Math.max(0,d.candles.length-Math.min(CH.N,d.candles.length));
      const v=Math.max(0,Math.min(mx,CH.drag.off+nb));if(v!==CH.off){CH.off=v;redraw()}return}
    const rect=box.getBoundingClientRect(),i=g.i0+Math.floor((e.clientX-rect.left-g.Ml)/g.step),tip=box.querySelector("#ctip"),cx=box.querySelector("#cx");
    if(!tip||!cx)return;
    if(i<g.i0||i>=g.i1){tip.textContent="";cx.setAttribute("opacity",0);return}
    const c=d.candles[i],p=nyParts(c[0]),f=v=>v.toFixed(d.digits),px=g.Ml+(i-g.i0+.5)*g.step;
    tip.textContent=`${p.wd} ${p.day} ${p.mon} ${p.hh}:${p.m} NY   O ${f(c[1])}  H ${f(c[2])}  L ${f(c[3])}  C ${f(c[4])}`;
    cx.setAttribute("x1",px);cx.setAttribute("x2",px);cx.setAttribute("opacity",1)});
  const end=()=>{CH.drag=null};box.addEventListener("pointerup",end);box.addEventListener("pointerleave",end);
  box.addEventListener("wheel",e=>{e.preventDefault();CH.N=Math.max(20,Math.min(300,Math.round(CH.N*(e.deltaY>0?1.15:.87))));redraw()},{passive:false});
  box.addEventListener("dblclick",()=>{CH.N=120;CH.off=0;drawChart()});
  window.addEventListener("resize",()=>{if(CH.d)redraw()});
})();
/* ---------------- top-down workflow ---------------- */
const TD={sym:"XAUUSD",busy:false};
const TDMARK={pass:"&#10003;",wait:"&hellip;",fail:"!",skip:"&ndash;"};
function tdTabs(){$("#tdtabs").innerHTML=PAIRS.map(p=>`<button data-sym="${p}" class="${p===TD.sym?"on":""}">${p}</button>`).join("");
  document.querySelectorAll("#tdtabs button").forEach(b=>b.onclick=()=>{TD.sym=b.dataset.sym;tdTabs();loadTopdown()})}
async function loadTopdown(){
  if(TD.busy)return;TD.busy=true;$("#tdrun").disabled=true;$("#tdrun").textContent="Running...";
  try{
    const q=new URLSearchParams({source:$("#source").value,symbol:TD.sym,offset:$("#offset").value||0,suffix:$("#suffix").value||""});
    const r=await fetch("/api/topdown?"+q),j=await r.json();
    if(j.error){$("#tdbox").innerHTML=`<div class="err">${esc(j.error)}</div>`;return}
    renderTopdown(j);
  }catch(e){$("#tdbox").innerHTML=`<div class="err">Top-down unavailable: ${esc(e)}</div>`}
  TD.busy=false;$("#tdrun").disabled=false;$("#tdrun").textContent="Run top-down";
}
function renderTopdown(j){
  const f=x=>Number(x).toFixed(j.dig),vc=j.verdict==="TRADE READY"?"buy":j.verdict==="NO TRADE"?"sell":"warn";
  ovset("td",`<span class="chip ${vc}" style="font-size:14px">${esc(j.verdict)}</span>`,`${j.sym} &middot; ${j.passed}/${j.total} steps`);
  $("#tdnote").textContent=`${j.sym} ${f(j.price)} | ${j.kz.clock} | ${j.passed}/${j.total} steps | updated ${j.updated}`;
  const pl=j.plan?`<div class="planbox"><b>${j.plan.side.toUpperCase()} LIMIT ${f(j.plan.entry)}</b> &nbsp; SL ${f(j.plan.sl)} &nbsp; TP ${f(j.plan.tp)} &nbsp; RR ${j.plan.rr} &nbsp;<span class="chip">${esc(j.plan.status)}</span>
    <div class="small" style="margin-top:4px">${esc(j.plan.model)}. ${esc(j.plan.risk_note)}</div></div>`:"";
  $("#tdbox").innerHTML=`<div class="verdict"><span class="chip ${vc}" style="font-size:14px;padding:4px 14px">${esc(j.verdict)}</span><span>${esc(j.why)}</span></div>`+
    j.stages.map(s=>`<div class="stp"><div class="dot ${s.status}">${TDMARK[s.status]}</div><div><div class="t"><b>${s.n}. ${esc(s.name)}</b> <span class="chip">${esc(s.tf)}</span> ${esc(s.summary)}</div>
    ${s.details.length?`<details><summary>Details</summary><ul>${s.details.map(d=>`<li>${esc(d)}</li>`).join("")}</ul></details>`:""}</div></div>`).join("")+pl;
}
tdTabs();$("#tdrun").onclick=loadTopdown;
/* ---------------- price action ---------------- */
let paBusy=false;
async function loadPA(){
  if(paBusy)return;paBusy=true;$("#parun").disabled=true;$("#parun").textContent="Scanning...";
  try{
    const q=new URLSearchParams({source:$("#source").value,mkt:$("#pamkt").value,tf:$("#patf").value,htf:"1",offset:$("#offset").value||0,suffix:$("#suffix").value||""});
    const r=await fetch("/api/pa?"+q),j=await r.json();
    if(j.error){$("#pabox").innerHTML=`<div class="err">${esc(j.error)}</div>`;return}
    renderPA(j);
  }catch(e){$("#pabox").innerHTML=`<div class="err">Price action unavailable: ${esc(e)}</div>`}
  paBusy=false;$("#parun").disabled=false;$("#parun").textContent="Scan price action";
}
function renderPA(j){
  const min=+$("#pamin").value,list=j.setups.filter(s=>s.score>=min).slice(0,20);
  $("#panote").textContent=`${list.length} of ${j.setups.length} setups shown | scanned ${j.scanned}`;
  ovset("pa",list.length,`${j.setups.length} found in total`);badge("pa",list.length);
  const errs=(j.errors||[]).map(e=>`<div class="err">${esc(e)}</div>`).join("");
  const cards=list.map(s=>{
    const buy=s.side==="buy",f=x=>Number(x).toFixed(j.digits[s.symbol]??5);
    return `<div class="setup ${buy?"buy":"sell"}"><div class="row"><b style="font-size:16px">${esc(s.symbol)}</b><span class="chip ${buy?"buy":"sell"}">${buy?"BUY":"SELL"} STOP</span>
      <span class="chip acc">${esc(s.tf)}</span><span class="chip">${esc(s.model)}</span><span class="chip">${esc(s.trigger)}</span>
      <span class="chip ${s.status==="PENDING"?"warn":"buy"}">${esc(s.status)}</span>${s.htf?`<span class="chip ${s.htf==="aligned"?"buy":s.htf==="counter"?"sell":""}">${esc(s.htf)} HTF</span>`:""}
      <span class="spacer"></span><span class="chip">Score ${s.score}/5</span><span class="small">trigger ${esc(s.formed)} NY</span></div>
      <div class="row" style="margin:8px 0"><span>Entry <b>${f(s.entry)}</b></span><span>SL <b>${f(s.sl)}</b></span><span>TP <b>${f(s.tp)}</b></span><span>RR <b>${s.rr}</b></span></div>
      <details class="why"><summary>Why this setup</summary><div class="notes">${esc(s.notes)}</div></details></div>`}).join("");
  const reads=$("#paread").checked?`<details style="margin-top:10px"><summary class="small" style="cursor:pointer;color:var(--accent)">Market read (${j.reads.length})</summary>
    ${j.reads.map(r=>`<div class="small" style="margin:6px 0"><b>${esc(r.symbol)}</b> ${esc(r.text)}</div>`).join("")}</details>`:"";
  $("#pabox").innerHTML=errs+(cards||`<div class="empty">No price-action setup at this score. Patience is a position.</div>`)+reads;
}
$("#parun").onclick=loadPA;["pamin","pamkt","patf"].forEach(i=>$("#"+i).onchange=loadPA);$("#paread").onchange=loadPA;
/* ---------------- SMT divergence ---------------- */
let smtBusy=false;
async function loadSMT(){
  if(smtBusy)return;smtBusy=true;$("#smrun").disabled=true;$("#smrun").textContent="Scanning...";
  try{
    const q=new URLSearchParams({source:$("#source").value,mkt:$("#smmkt").value,tf:$("#smtf").value,age:$("#smage").value,offset:$("#offset").value||0,suffix:$("#suffix").value||""});
    const r=await fetch("/api/smt?"+q),j=await r.json();
    if(j.error){$("#smtbox").innerHTML=`<div class="err">${esc(j.error)}</div>`;return}
    renderSMT(j);
  }catch(e){$("#smtbox").innerHTML=`<div class="err">SMT unavailable: ${esc(e)}</div>`}
  smtBusy=false;$("#smrun").disabled=false;$("#smrun").textContent="Scan SMT";
}
function renderSMT(j){
  const min=+$("#smmin").value,list=j.divs.filter(d=>d.score>=min).slice(0,15),tri=(j.triples||[]).filter(t=>t.score>=min).slice(0,6);
  $("#smtnote").textContent=`${j.clock} | ${j.in_kz?"inside a killzone":"outside killzones"} | ${list.length} of ${j.divs.length} pair signals, ${tri.length} triple | scanned ${j.scanned}`;
  ovset("smt",list.length+tri.length,`${tri.length} triple, ${list.length} pair`);badge("smt",list.length+tri.length);
  const errs=(j.errors||[]).map(e=>`<div class="err">${esc(e)}</div>`).join("");
  const ageT=m=>m>=60?Math.floor(m/60)+"h "+(m%60)+"m":m+"m";
  const triH=tri.map(t=>{const bull=t.side==="bullish";return `<div class="setup ${bull?"buy":"sell"}"><div class="row"><b style="font-size:16px">NQ / ES / YM</b>
      <span class="chip acc">TRIPLE SMT</span><span class="chip ${bull?"buy":"sell"}">${bull?"BULLISH":"BEARISH"}</span><span class="chip acc">${esc(t.tf)}</span><span class="chip">${esc(t.kind)}: ${esc(t.level)}</span>
      ${t.in_kz?'<span class="chip buy">Killzone</span>':""}${t.silver_bullet?'<span class="chip buy">Silver Bullet window</span>':""}${t.reclaimed?'<span class="chip buy">Reclaimed</span>':""}
      <span class="spacer"></span><span class="small">${esc(t.formed)} NY &middot; ${ageT(t.age_min)} ago</span></div>
      <details class="why" style="margin:6px 0"><summary>Why this signal</summary><div class="notes">${esc(t.notes)}</div></details>
      <div class="row">${t.holders.map(s=>`<span class="chip ${t.action}">${s} ${t.action.toUpperCase()}</span>`).join("")}<span class="small">${esc(t.takers.join(" & "))} took the liquidity.</span></div></div>`}).join("");
  $("#smtbox").innerHTML=errs+triH+(list.length?list.map(d=>{
    const bull=d.side==="bullish",age=d.age_min>=60?Math.floor(d.age_min/60)+"h "+(d.age_min%60)+"m":d.age_min+"m";
    return `<div class="setup ${bull?"buy":"sell"}"><div class="row"><b style="font-size:16px">${esc(d.a)} / ${esc(d.b)}</b>
      <span class="chip ${bull?"buy":"sell"}">${bull?"BULLISH":"BEARISH"} SMT</span><span class="chip acc">${esc(d.tf)}</span><span class="chip">${esc(d.kind)}: ${esc(d.level)}</span>
      ${d.rel<0?'<span class="chip">inverse pair</span>':""}${d.in_kz?'<span class="chip buy">Killzone</span>':""}${d.silver_bullet?'<span class="chip buy">Silver Bullet window</span>':""}${d.reclaimed?'<span class="chip buy">Reclaimed</span>':'<span class="chip warn">Not reclaimed</span>'}
      <span class="spacer"></span><span class="chip">Score ${d.score}/4</span><span class="small">${esc(d.formed)} NY &middot; ${age} ago</span></div>
      <details class="why" style="margin:6px 0"><summary>Why this signal</summary><div class="notes">${esc(d.notes)}</div></details>
      <div class="row">${Object.entries(d.lean).map(([s,v])=>`<span class="chip ${v}">${s} ${v.toUpperCase()}</span>`).join("")}
      <span class="small">${esc(d.holder)} held (stronger side), ${esc(d.taker)} took the liquidity.</span></div></div>`}).join(""):
    (tri.length?"":`<div class="empty">No SMT divergence at this score / time window. Common: correlated markets usually move together.</div>`));
}
$("#smrun").onclick=loadSMT;$("#smmin").onchange=loadSMT;$("#smmkt").onchange=loadSMT;$("#smtf").onchange=loadSMT;$("#smage").onchange=loadSMT;
const IMPCLS={High:"sell",Medium:"warn",Holiday:"",Low:""};
const LABELS={XAUUSD:"Gold (XAUUSD)",USDJPY:"USD/JPY",EURUSD:"EUR/USD",GBPUSD:"GBP/USD",MACRO:"Market-wide (Fed, inflation, USD)"};
let news=null;
function cdtxt(ts){const s=Math.round(ts-Date.now()/1000),a=Math.abs(s),h=Math.floor(a/3600),m=Math.floor(a%3600/60);
  const t=h?`${h}h ${String(m).padStart(2,"0")}m`:`${m}m`;return a<60?"now":s>0?`in ${t}`:`${t} ago`;}
function tickCd(){document.querySelectorAll(".cd").forEach(e=>{e.textContent=cdtxt(+e.dataset.ts)})}
const nyT=ts=>new Intl.DateTimeFormat("en-GB",{timeZone:"America/New_York",weekday:"short",hour:"2-digit",minute:"2-digit",hour12:false}).format(new Date(ts*1000));
const localT=ts=>new Date(ts*1000).toLocaleString([],{weekday:"short",hour:"2-digit",minute:"2-digit"});
async function loadNews(){
  const syms=[...document.querySelectorAll("#pairs input:checked")].map(i=>i.value).join(",");
  try{const r=await fetch("/api/news?"+new URLSearchParams({source:$("#source").value,symbols:syms,impact:$("#nimp").value}));
    news=await r.json(); renderNews();}
  catch(e){$("#nnote").textContent="news unavailable: "+e}
}
function renderNews(){
  if(!news||news.error){$("#events").innerHTML=`<div class="err">${esc((news&&news.error)||"news unavailable")}</div>`;return}
  const nerr=(news.errors||[]).map(e=>`<div class="err">${esc(e)}</div>`).join("");
  $("#nrisk").innerHTML=Object.entries(news.risk||{}).map(([sym,r])=>`<div class="np ${r.status}" title="${esc(r.msg)}"><i></i><b>${sym}</b> ${r.status==="CLEAR"?"news clear":esc(r.msg)}</div>`).join("");
  $("#nnote").textContent=`updated ${news.fetched} (calendar cached up to 15 min)`;
  {const nx=(news.events||[]).find(e=>e.impact==="High"&&e.ts>Date.now()/1000);ovset("news",nx?esc(nx.ccy)+" "+esc(nx.title):"None",nx?`<span class="cd" data-ts="${nx.ts}"></span>`:"nothing high-impact in 48h")}
  const now=Date.now()/1000;
  $("#events").innerHTML=nerr+(news.events.length?news.events.map(e=>`<div class="ev ${e.ts<now?"past":""}">
    <div><b>${nyT(e.ts)}</b> <span class="small">NY</span><div class="small">${localT(e.ts)} yours</div></div>
    <div><span class="chip">${esc(e.ccy)}</span></div>
    <div><span class="chip ${IMPCLS[e.impact]||""}">${esc(e.impact)}</span>${e.key?' <span class="chip acc">KEY</span>':""}</div>
    <div>${esc(e.title)}<div class="small">${e.forecast?"Forecast "+esc(e.forecast)+" ":""}${e.previous?"Previous "+esc(e.previous)+" ":""}&middot; affects ${e.pairs.join(", ")||"-"}</div></div>
    <div class="cd small" data-ts="${e.ts}"></div></div>`).join(""):`<div class="empty">No matching events in the next 48 hours (or the calendar feed is unavailable).</div>`);
  $("#heads").innerHTML=Object.entries(news.headlines||{}).map(([k,h])=>{
    const cls=k==="MACRO"?"":h.summary.startsWith("Bullish")?"buy":h.summary.startsWith("Bearish")?"sell":"";
    return `<div class="bias"><div class="top"><span class="sym">${LABELS[k]||k}</span></div>
      <div style="margin:6px 0 2px"><span class="chip ${cls}">${esc(h.summary)}</span></div><div class="small" style="margin-bottom:8px">${esc(h.detail)}</div>
      ${h.items.length?h.items.slice(0,4).map(i=>`<div style="margin:7px 0;font-size:13.5px"><a href="${/^https?:/.test(i.link)?esc(i.link):"#"}" target="_blank" rel="noopener" style="color:var(--ink);text-decoration:none">${esc(i.title)}</a>
      <div class="small">${esc(i.src)} &middot; ${i.ts?cdtxt(i.ts):""}</div></div>`).join(""):'<div class="small">No recent headlines.</div>'}</div>`}).join("");
  tickCd();
}
$("#nimp").onchange=loadNews;
function arm(){clearInterval(timer);const s=+$("#auto").value;if(s)timer=setInterval(scan,s*1000)}
$("#auto").onchange=arm; $("#go").onclick=scan;
["flt","top"].forEach(i=>$("#"+i).onchange=()=>data&&render());
["source","htf","tf","strat"].forEach(i=>$("#"+i).addEventListener("change",()=>{first=true;seen.clear()}));
$("#csv").onclick=()=>{
  if(!data||!data.setups.length)return alert("No setups to export yet.");
  const cols=["symbol","tf","model","side","entry","sl","tp","rr","status","formed","score","htf","notes"];
  const rows=[cols.join(",")].concat(data.setups.map(s=>cols.map(c=>`"${String(s[c]).replace(/"/g,'""')}"`).join(",")));
  const a=document.createElement("a");a.href=URL.createObjectURL(new Blob([rows.join("\n")],{type:"text/csv"}));
  a.download="ict_setups_"+new Date().toISOString().slice(0,10)+".csv";a.click();
};
/* ---------------- tabs / overview ---------------- */
const TABS=[["overview","Overview"],["chart","Chart"],["setups","Setups"],["topdown","Top-down"],["pa","Price action"],["smt","SMT"],["news","News"]];
$("#nav").innerHTML=TABS.map(([k,l])=>`<button data-tab="${k}">${l}<span class="bd" style="display:none"></span></button>`).join("");
function go(t){
  if(!TABS.some(x=>x[0]===t))t="overview";
  document.querySelectorAll(".tab").forEach(e=>e.classList.toggle("on",e.id==="tab-"+t));
  document.querySelectorAll("#nav button").forEach(b=>b.classList.toggle("on",b.dataset.tab===t));
  try{history.replaceState(null,"","#"+t)}catch(e){}
  if(t==="chart"&&CH.d)drawChart();
}
function badge(t,n){const b=document.querySelector(`#nav [data-tab="${t}"] .bd`);if(b){b.textContent=n||"";b.style.display=n?"":"none"}}
function ovset(k,v,s){const a=$("#ov-"+k+"-v"),b=$("#ov-"+k+"-s");if(a)a.innerHTML=v;if(b)b.innerHTML=s}
document.addEventListener("click",e=>{
  const n=e.target.closest("#nav button");if(n){go(n.dataset.tab);return}
  const t=e.target.closest("[data-go]");if(t){go(t.dataset.go);return}
  const c=e.target.closest("[data-copy]");if(c){try{navigator.clipboard.writeText(c.dataset.copy)}catch(x){}c.textContent="Copied";return}
  const l=document.querySelector(".layers[open]");if(l&&!l.contains(e.target))l.removeAttribute("open");
});
$("#gear").onclick=()=>{const d=$("#drawer");d.hidden=!d.hidden;$("#gear").classList.toggle("on",!d.hidden)};
go((location.hash||"").slice(1));
arm(); armChart(); loadChart(); scan(); loadTopdown(); loadPA(); loadSMT();
</script></body></html>
"""


def main():
    port = free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"ICT Dashboard running at {url}   (Ctrl+C to stop)")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
