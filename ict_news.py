#!/usr/bin/env python3
"""
News analysis helper for the ICT dashboard (no extra libraries needed).

1. Economic calendar : Forex Factory weekly feed (free, via faireconomy.media).
                       Current week only; forecast + previous, but NO "actual" values.
2. News risk         : per pair CLEAR / CAUTION / BLACKOUT from high-impact releases.
3. Headlines         : Google News RSS per pair + a market-wide group, with a ROUGH
                       keyword "tone" tag. Tone is a heuristic, not a trading signal.

Used by ict_app.py. Run `python ict_news.py` to print a quick text report
(`python ict_news.py demo` uses synthetic data).
Educational tool. Not financial advice.
"""
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

CAL_URLS = ["https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            "https://cdn-nfs.faireconomy.media/ff_calendar_thisweek.json"]
PAIR_CCY = {"XAUUSD": {"USD"}, "USDJPY": {"USD", "JPY"}, "EURUSD": {"EUR", "USD"}, "GBPUSD": {"GBP", "USD"}}
QUERIES = {"XAUUSD": "gold price", "USDJPY": "USD/JPY", "EURUSD": "EUR/USD", "GBPUSD": "GBP/USD",
           "MACRO": "Federal Reserve OR inflation OR jobs report OR dollar index"}
KEY = re.compile(r"non-farm|nfp|cpi|fomc|federal funds|interest rate|rate statement|press conference|"
                 r"core pce|unemployment rate|gdp|retail sales|powell|lagarde|bailey|ueda|boj|ecb|boe", re.I)

BLACKOUT_SECS = 30 * 60        # 30 min before and after a high-impact release
CAUTION_SECS = 2 * 3600        # high-impact release within 2 hours
CAL_TTL, NEWS_TTL = 900, 600

_cache = {}


def cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        val = fn()
    except Exception:
        if hit:
            return hit[1]          # serve stale data rather than nothing
        raise
    _cache[key] = (time.time(), val)
    return val


def http_get(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (ICT-Dashboard)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# --------------------------------------------------------------- calendar ----
def parse_calendar(raw):
    out = []
    for e in raw:
        try:
            dt = datetime.fromisoformat(e["date"])
            if dt.tzinfo is None:
                continue
            ccy = e.get("country", "").upper()
            out.append(dict(ts=int(dt.timestamp()), title=e.get("title", ""), ccy=ccy,
                            impact=e.get("impact", "Low"), forecast=e.get("forecast", "") or "",
                            previous=e.get("previous", "") or "",
                            key=e.get("impact") == "High" and bool(KEY.search(e.get("title", ""))),
                            pairs=[s for s, c in PAIR_CCY.items() if ccy in c]))
        except Exception:
            continue
    return sorted(out, key=lambda x: x["ts"])


def fetch_calendar():
    last = None
    for url in CAL_URLS:
        try:
            return parse_calendar(json.loads(http_get(url)))
        except Exception as e:
            last = e
    raise RuntimeError(f"calendar feed unavailable ({last})")


def demo_calendar():
    n = int(time.time())
    rows = [
        (-40 * 60, "Flash Manufacturing PMI", "EUR", "High", "47.2", "46.9"),
        (22 * 60, "Core CPI m/m", "USD", "High", "0.3%", "0.3%"),
        (95 * 60, "FOMC Member Speaks", "USD", "Medium", "", ""),
        (3 * 3600, "BOJ Press Conference", "JPY", "High", "", ""),
        (5 * 3600, "UK Retail Sales m/m", "GBP", "Medium", "0.4%", "-0.2%"),
        (20 * 3600, "Non-Farm Employment Change", "USD", "High", "165K", "142K"),
        (26 * 3600, "ECB President Lagarde Speaks", "EUR", "High", "", ""),
        (30 * 3600, "Bank Holiday", "JPY", "Holiday", "", ""),
    ]
    return parse_calendar([dict(
        title=t, country=c, impact=i, forecast=f, previous=p,
        date=datetime.fromtimestamp(n + off).astimezone().isoformat()) for off, t, c, i, f, p in rows])


def get_events(source="yahoo"):
    if source == "demo":
        return demo_calendar()
    return cached("cal", CAL_TTL, fetch_calendar)


def risk(events, symbols, now=None):
    now = now or time.time()
    out = {}
    for sym in symbols:
        cc = PAIR_CCY[sym]
        hi = [e for e in events if e["ccy"] in cc and e["impact"] == "High"]
        md = [e for e in events if e["ccy"] in cc and e["impact"] == "Medium"]
        status, msg = "CLEAR", f"No high-impact {'/'.join(sorted(cc))} releases within 2 hours."
        soon = [e for e in hi if 0 <= e["ts"] - now <= BLACKOUT_SECS]
        recent = [e for e in hi if -BLACKOUT_SECS <= e["ts"] - now < 0]
        near_hi = [e for e in hi if BLACKOUT_SECS < e["ts"] - now <= CAUTION_SECS]
        near_md = [e for e in md if 0 <= e["ts"] - now <= BLACKOUT_SECS]
        if soon:
            e = soon[0]
            status, msg = "BLACKOUT", f"{e['ccy']} {e['title']} in {max(1, round((e['ts'] - now) / 60))}m. Spreads widen, stand aside."
        elif recent:
            e = recent[-1]
            status, msg = "BLACKOUT", f"{e['ccy']} {e['title']} released {round((now - e['ts']) / 60)}m ago. Volatility elevated."
        elif near_hi:
            e = near_hi[0]
            status, msg = "CAUTION", f"{e['ccy']} {e['title']} in {round((e['ts'] - now) / 60)}m. Plan to be flat before it."
        elif near_md:
            e = near_md[0]
            status, msg = "CAUTION", f"Medium-impact {e['ccy']} {e['title']} in {round((e['ts'] - now) / 60)}m."
        nxt = next((e for e in hi if e["ts"] > now), None)
        out[sym] = dict(status=status, msg=msg, next=nxt)
    return out


# -------------------------------------------------------------- headlines ----
UP = re.compile(r"\b(rise|rises|rising|rally|rallies|surge|surges|jump|jumps|climb|climbs|gain|gains|soar|soars|"
                r"higher|advance|advances|rebound|rebounds|strengthen|strengthens)\b", re.I)
DOWN = re.compile(r"\b(fall|falls|falling|drop|drops|slump|slumps|slide|slides|tumble|tumbles|plunge|plunges|"
                  r"retreat|retreats|lose|loses|lower|decline|declines|weaken|weakens|slip|slips|sink|sinks|dip|dips)\b", re.I)
HAWK = re.compile(r"\b(rate hike|hike|hikes|hotter|hot inflation|sticky|hawkish|higher for longer|strong jobs|"
                  r"stronger than expected|beats|yields rise|yields jump)\b", re.I)
DOVE = re.compile(r"\b(rate cut|cut|cuts|cooler|cooling|dovish|weak jobs|slowdown|softer|weaker than expected|"
                  r"misses|yields fall|recession)\b", re.I)


# Which word in a headline is the pair's BASE (price up = pair up) and which is the QUOTE (price up = pair down)
SUBJ = {"XAUUSD": (r"gold|xau", None), "USDJPY": (r"dollar|usd|greenback", r"yen|jpy"),
        "EURUSD": (r"euro|eur\b", r"dollar|usd|greenback"), "GBPUSD": (r"sterling|pound|gbp|cable", r"dollar|usd|greenback")}
PAIRRX = {"XAUUSD": r"xau/?usd", "USDJPY": r"usd/jpy", "EURUSD": r"eur/usd", "GBPUSD": r"gbp/usd"}


def tone(title, key=None):
    """+1 / -1 = the headline says the PAIR moved up / down; 0 = unclear. Rough keyword heuristic."""
    if key not in SUBJ:
        return 0
    u, d = len(UP.findall(title)), len(DOWN.findall(title))
    move = 1 if u > d else -1 if d > u else 0
    if not move:
        return 0
    base, quote = SUBJ[key]
    t = title.lower()
    if re.search(PAIRRX[key], t):
        return move
    mb = re.search(base, t)
    mq = re.search(quote, t) if quote else None
    if mb and mq:                               # both named: the first one mentioned is the subject
        return move if mb.start() < mq.start() else -move
    return move if mb else -move if mq else 0


def lean(title):
    h, d = len(HAWK.findall(title)), len(DOVE.findall(title))
    return "hawk" if h > d else "dove" if d > h else ""


def fetch_rss(query, key=None):
    url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(query + " when:2d") +
           "&hl=en-US&gl=US&ceid=US:en")
    root = ET.fromstring(http_get(url))
    items, seen = [], set()
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        src = (it.findtext("source") or "").strip()
        if src and title.endswith(" - " + src):
            title = title[: -(len(src) + 3)]
        if not title or title.lower() in seen:
            continue
        seen.add(title.lower())
        try:
            ts = int(parsedate_to_datetime(it.findtext("pubDate")).timestamp())
        except Exception:
            ts = 0
        items.append(dict(title=title, link=it.findtext("link") or "", src=src, ts=ts,
                          tone=tone(title, key), lean=lean(title)))
    items.sort(key=lambda x: -x["ts"])
    return items[:6]


def demo_headlines(key):
    n = int(time.time())
    base = {
        "XAUUSD": [("Gold climbs as traders await US inflation data", 1), ("Gold slips from record high on profit-taking", -1),
                   ("Central bank buying keeps gold supported", 0)],
        "USDJPY": [("Yen slides as BOJ signals no rush to tighten", 1), ("USD/JPY steady near 150 ahead of CPI", 0)],
        "EURUSD": [("Euro falls as PMI data disappoints", -1), ("EUR/USD holds range before Fed speakers", 0)],
        "GBPUSD": [("Sterling rallies on stronger wage growth", 1), ("Pound dips as UK retail sales miss", -1)],
        "MACRO": [("Fed officials signal rate cut possible if inflation cools", 0), ("Sticky inflation keeps higher for longer in play", 0),
                  ("Strong jobs data lifts Treasury yields", 0)],
    }[key]
    return [dict(title=t, link="#", src="Demo wire", ts=n - (i + 1) * 1800, tone=tone(t, key), lean=lean(t))
            for i, (t, _) in enumerate(base)]


def get_headlines(symbols, source="yahoo"):
    out, errors = {}, []
    for key in list(symbols) + ["MACRO"]:
        try:
            items = demo_headlines(key) if source == "demo" else cached("rss:" + key, NEWS_TTL, lambda k=key: fetch_rss(QUERIES[k], k))
        except Exception as e:
            errors.append(f"{key} headlines: {e}")
            continue
        if key == "MACRO":
            h, d = sum(i["lean"] == "hawk" for i in items), sum(i["lean"] == "dove" for i in items)
            summary = ("Hawkish lean (tends to support USD, pressure gold)" if h > d else
                       "Dovish lean (tends to pressure USD, support gold)" if d > h else "No clear policy lean")
            out[key] = dict(items=items, summary=summary, detail=f"{h} hawkish / {d} dovish headlines")
        else:
            up, dn = sum(i["tone"] > 0 for i in items), sum(i["tone"] < 0 for i in items)
            summary = "Bullish tone" if up > dn else "Bearish tone" if dn > up else "Mixed / neutral tone"
            out[key] = dict(items=items, summary=summary, detail=f"{up} up-move / {dn} down-move headlines")
    return out, errors


def build(symbols, source="yahoo", impacts=("High",), hours_ahead=48, hours_back=3):
    """Everything the dashboard's news section needs."""
    res = dict(events=[], risk={}, headlines={}, errors=[], fetched=time.strftime("%H:%M:%S"))
    now = time.time()
    try:
        cal = get_events(source)
        wanted = set(impacts) | {"Holiday"}
        rel = {c for s in symbols for c in PAIR_CCY[s]}
        res["events"] = [e for e in cal if e["ccy"] in rel and e["impact"] in wanted
                         and -hours_back * 3600 <= e["ts"] - now <= hours_ahead * 3600]
        res["risk"] = risk(cal, symbols, now)
    except Exception as e:
        res["errors"].append(f"calendar: {e}")
    res["headlines"], errs = get_headlines(symbols, source)
    res["errors"] += errs
    return res


if __name__ == "__main__":
    src = "demo" if "demo" in sys.argv else "yahoo"
    syms = list(PAIR_CCY)
    r = build(syms, src, impacts=("High", "Medium"))
    print("NEWS RISK")
    for s, v in r["risk"].items():
        print(f"  {s:7} {v['status']:9} {v['msg']}")
    print("\nUPCOMING EVENTS (next 48h, UTC offset from your PC)")
    for e in r["events"]:
        t = datetime.fromtimestamp(e["ts"]).strftime("%a %H:%M")
        print(f"  {t}  {e['ccy']}  {e['impact']:8} {e['title']}  fcst {e['forecast'] or '-'}  prev {e['previous'] or '-'}")
    print("\nHEADLINES")
    for k, v in r["headlines"].items():
        print(f"  [{k}] {v['summary']} ({v['detail']})")
        for i in v["items"][:3]:
            print(f"     - {i['title']}")
    for e in r["errors"]:
        print("!", e)
