#!/usr/bin/env python3
"""Compute CLC gamma levels from CBOE's free delayed SPX chain and write
levels.csv in BookMap/Cloud-Levels format (ES + MES rows).

Sources (all free, no key):
  - Chain: https://cdn.cboe.com/api/global/delayed_quotes/options/_SPX.json (~15m delayed)
  - ES futures quote + daily history: Yahoo Finance chart API (free)
  SPX gamma is computed from the CBOE chain, then translated to ES price
  space via the live ES/SPX ratio (standard proxy approach).
Method (validated 2026-10-02 against published GEX levels):
  - 0-1 DTE window; per-strike $B gamma = OI * gamma * spot^2 / 1e9
  - Call wall = max call-side gamma at strikes >= spot
  - Put wall  = max put-side gamma at strikes <= spot
  - Flip = midpoint of the adjacent strike pair whose net gamma straddles
    zero, nearest to spot
  - Net regime = sign of total net gamma (calls minus puts)
"""
import csv
import json
import sys
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0"}
SYMBOLS = ("ES", "MES")
COLORS = {
    "call_wall": ("#FFFFFF", "#C62828"),
    "flip": ("#000000", "#FFD740"),
    "spot": ("#FFFFFF", "#616161"),
    "put_wall": ("#FFFFFF", "#2E7D32"),
    "structural": ("#FFFFFF", "#1565C0"),
}


def fetch_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.load(r)


def fetch_text(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def parse_opt(sym):
    # root(4) + YYMMDD(6) + C/P + strike(8 digits / 1000), e.g. SPXW261001C03200000
    exp = date(2000 + int(sym[4:6]), int(sym[6:8]), int(sym[8:10]))
    return exp, sym[10], int(sym[11:]) / 1000.0


def yahoo_es():
    """ES futures quote + daily bars from Yahoo Finance (free, no key)."""
    url = "https://query1.finance.yahoo.com/v8/finance/chart/ES=F?range=5d&interval=1d"
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    res = data["chart"]["result"][0]
    meta = res["meta"]
    spot = meta.get("regularMarketPrice")
    quote = res["indicators"]["quote"][0]
    bars = []
    for i, ts in enumerate(res["timestamp"]):
        d = datetime.fromtimestamp(ts, ZoneInfo("America/New_York")).date().isoformat()
        bars.append((d, quote["high"][i], quote["low"][i], quote["close"][i]))
    return spot, bars


def main():
    et_today = datetime.now(ZoneInfo("America/New_York")).date()

    # --- ES futures spot + prior-day HLC from Yahoo (free) ---
    spot = None
    pdh = pdl = pdc = None
    try:
        spot, bars = yahoo_es()
        done = [b for b in bars if b[0] < et_today.isoformat() and b[1]]
        if done:
            _, pdh, pdl, pdc = done[-1]
    except Exception as e:
        print(f"Yahoo ES fetch failed: {e}", file=sys.stderr)

    # --- SPX chain from CBOE (free, keyless, ~15m delayed) ---
    try:
        raw = fetch_json("https://cdn.cboe.com/api/global/delayed_quotes/options/_SPX.json")
    except Exception as e:
        print(f"CBOE chain fetch failed: {e} - keeping previous levels.csv", file=sys.stderr)
        return 0
    opts = raw["data"]["options"]
    # SPX spot (from the chain itself) is used for strike selection;
    # ES spot (Yahoo) only for the ES/SPX translation ratio and reference row.
    try:
        spx_spot = float(raw["data"].get("close"))
    except Exception:
        spx_spot = None
    if spx_spot is None:
        print("no SPX spot - keeping previous levels.csv", file=sys.stderr)
        return 0
    if spot is None:  # Yahoo failed: fall back to SPX close, no translation
        spot = spx_spot

    call_gex = defaultdict(float)
    put_gex = defaultdict(float)
    for o in opts:
        try:
            exp, typ, strike = parse_opt(o["option"])
        except Exception:
            continue
        dte = (exp - et_today).days
        if dte < 0 or dte > 1:  # 0-1 DTE: the gamma that matters intraday
            continue
        g = (o.get("open_interest") or 0) * (o.get("gamma") or 0)
        if typ == "C":
            call_gex[strike] += g
        else:
            put_gex[strike] += g

    strikes = sorted(set(call_gex) | set(put_gex))
    if not strikes:
        print("no strikes parsed - keeping previous levels.csv", file=sys.stderr)
        return 0
    norm = spx_spot ** 2 / 1e9  # $B per 1% move
    net = {k: (call_gex[k] - put_gex[k]) * norm for k in strikes}

    cw = max([k for k in strikes if k >= spx_spot], key=lambda k: call_gex[k] * norm)
    pw = max([k for k in strikes if k <= spx_spot], key=lambda k: put_gex[k] * norm)

    flip = None
    for a, b in zip(strikes, strikes[1:]):
        if net[a] == 0 or net[a] * net[b] < 0:
            mid = (a + b) / 2
            if flip is None or abs(mid - spx_spot) < abs(flip - spx_spot):
                flip = mid

    net_total = sum(net.values())
    regime = "positive-gamma (dampening)" if net_total > 0 else "negative-gamma (amplifying)"

    # SPX levels -> ES levels: ES futures trade at a premium/discount to SPX.
    # Translate via the live ES/SPX ratio so walls land on his ES chart.
    adj = spot / spx_spot

    def es_level(x):
        return round(x * adj * 4) / 4  # ES tick = 0.25

    levels = [
        (es_level(cw), f"Call Wall {es_level(cw):.2f} (gamma)", "call_wall"),
        (es_level(flip), f"Gamma Flip {es_level(flip):.2f}", "flip") if flip else None,
        (round(spot * 4) / 4, f"ES spot {spot:.2f}", "spot"),
        (es_level(pw), f"Put Wall {es_level(pw):.2f} (gamma)", "put_wall"),
    ]
    if pdh and pdl:
        levels.append((round(pdh * 4) / 4, f"Prior Day High {pdh:.2f}", "structural"))
        levels.append((round(pdl * 4) / 4, f"Prior Day Low {pdl:.2f}", "structural"))
    levels = [lv for lv in levels if lv]

    header = ("Automap Command,Symbol,Price Level,Note,Foreground Color,"
              "Background Color,Diameter,Text Alignment,Draw Note Price Horizontal Line")
    with open("levels.csv", "w", newline="") as f:
        f.write(header + "\n")
        for sym in SYMBOLS:
            for price, note, kind in levels:
                fg, bg = COLORS[kind]
                f.write(f",{sym},{price:.2f},{note},{fg},{bg},1,center,TRUE\n")

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")
    print(f"[{stamp}Z] spot={spot:.2f} call_wall={es_level(cw):.2f} "
          f"put_wall={es_level(pw):.2f} flip={es_level(flip) if flip else None} "
          f"net={net_total:+.2f}B {regime} pdh={pdh} pdl={pdl}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
