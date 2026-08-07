# -*- coding: utf-8 -*-
"""5 enstruman icin guncel SMC seviyelerini cikarir (cizim icin)."""
import sys, json, urllib.request
sys.path.insert(0, r"C:\Users\USER\smc-watcher")
from smc_watch import (pivots, find_fvgs, find_sweeps, dealing_range,
                       htf_bias, fetch_binance, fetch_yahoo, resample_to_1h)

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def yahoo(kod, interval, rng):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{kod}"
           f"?interval={interval}&range={rng}")
    d = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25))
    r = d["chart"]["result"][0]; q = r["indicators"]["quote"][0]
    out = []
    for i, t in enumerate(r["timestamp"]):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c):
            continue
        out.append({"t": int(t), "o": o, "h": h, "l": l, "c": c, "v": float(q["volume"][i] or 0)})
    return out


def rapor(ad, htf, ltf, rng_bar=40):
    fiyat = ltf[-1]["c"]
    bias = htf_bias(htf)
    hi, lo, eq = dealing_range(htf, rng_bar)
    ph, pl = pivots(htf, 2)
    fvg = find_fvgs(htf, 60)
    fvg_l = find_fvgs(ltf, 80)
    sw = find_sweeps(ltf, 10, 30)
    bull_h = [x for x in fvg if x["tip"] == "bull"][-3:]
    bear_h = [x for x in fvg if x["tip"] == "bear"][-2:]
    bull_l = [x for x in fvg_l if x["tip"] == "bull"][-2:]
    bear_l = [x for x in fvg_l if x["tip"] == "bear"][-2:]

    print("=" * 66)
    print(f"{ad}   FIYAT {fiyat:,.2f}".replace(",", "."))
    print(f"  bias={bias}   aralik {lo:,.2f}-{hi:,.2f}   EQ {eq:,.2f}   "
          f"konum %{(fiyat-lo)/(hi-lo)*100:.0f}")
    print(f"  swing tepeler: {[round(p[1],2) for p in ph[-3:]]}")
    print(f"  swing dipler : {[round(p[1],2) for p in pl[-3:]]}")
    print(f"  HTF bull FVG : {[(round(x['alt'],2), round(x['ust'],2)) for x in bull_h]}")
    print(f"  HTF bear FVG : {[(round(x['alt'],2), round(x['ust'],2)) for x in bear_h]}")
    print(f"  LTF bull FVG : {[(round(x['alt'],2), round(x['ust'],2)) for x in bull_l]}")
    print(f"  LTF bear FVG : {[(round(x['alt'],2), round(x['ust'],2)) for x in bear_l]}")
    if sw["ssl"]: print(f"  >> SSL supuruldu {sw['ssl_seviye']:,.2f}")
    if sw["bsl"]: print(f"  >> BSL supuruldu {sw['bsl_seviye']:,.2f}")


# --- BTC
b15 = fetch_binance("BTCUSDT", "15m", 300)
b1h = fetch_binance("BTCUSDT", "1h", 200)
rapor("BTCUSDT", b1h, b15, 96)

# --- NQ
n15 = fetch_yahoo("NQ%3DF", "15m", "5d")
n1h = fetch_yahoo("NQ%3DF", "60m", "1mo")
rapor("NQ1!", n1h, n15, 96)

# --- BIST
for kod in ["ASELS", "TUPRS", "BIMAS"]:
    g = yahoo(f"{kod}.IS", "1d", "6mo")
    s = yahoo(f"{kod}.IS", "60m", "1mo")
    rapor(kod, g, s, 40)
