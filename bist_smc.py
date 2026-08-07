# -*- coding: utf-8 -*-
"""Secilen BIST hisseleri icin SMC yapi analizi (tek yonlu / long)."""
import sys, json, urllib.request
sys.path.insert(0, r"C:\Users\USER\smc-watcher")
from smc_watch import pivots, find_fvgs, find_sweeps, dealing_range, htf_bias

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
SECIM = {"ASELS": "Savunma", "TUPRS": "Rafineri", "BIMAS": "Perakende"}


def cek(kod, interval, rng):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{kod}.IS"
           f"?interval={interval}&range={rng}")
    d = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25))
    r = d["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    out = []
    for i, t in enumerate(r["timestamp"]):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c):
            continue
        out.append({"t": int(t), "o": o, "h": h, "l": l, "c": c,
                    "v": float(q["volume"][i] or 0)})
    return out


for kod, sektor in SECIM.items():
    g = cek(kod, "1d", "6mo")
    s = cek(kod, "60m", "1mo")
    fiyat = g[-1]["c"]

    bias = htf_bias(g)
    hi, lo, eq = dealing_range(g, 40)          # son 40 gun dealing range
    ph, pl = pivots(g, 2)
    fvg_g = [x for x in find_fvgs(g, 60)]
    fvg_s = [x for x in find_fvgs(s, 80)]
    sw = find_sweeps(g, 8, 25)

    print("=" * 62)
    print(f"{kod}  ({sektor})   fiyat {fiyat:.2f}")
    print(f"  HTF bias (gunluk)   : {bias}")
    print(f"  40 gun aralik       : {lo:.2f} - {hi:.2f}   EQ {eq:.2f}")
    print(f"  Konum               : {'PREMIUM' if fiyat > eq else 'DISCOUNT'} "
          f"({(fiyat-lo)/(hi-lo)*100:.0f}%)")
    print(f"  Son swing tepeler   : {[round(p[1],2) for p in ph[-3:]]}")
    print(f"  Son swing dipler    : {[round(p[1],2) for p in pl[-3:]]}")
    if sw["ssl"]:
        print(f"  >> SSL supuruldu    : {sw['ssl_seviye']:.2f}")
    if sw["bsl"]:
        print(f"  >> BSL supuruldu    : {sw['bsl_seviye']:.2f}")
    bull_g = [x for x in fvg_g if x["tip"] == "bull"]
    print(f"  Gunluk bullish FVG  : {[(round(x['alt'],2), round(x['ust'],2)) for x in bull_g[-3:]]}")
    bull_s = [x for x in fvg_s if x["tip"] == "bull"]
    print(f"  Saatlik bullish FVG : {[(round(x['alt'],2), round(x['ust'],2)) for x in bull_s[-4:]]}")
    # ortalama gunluk hareket
    ar = sum((b["h"]-b["l"])/b["c"] for b in g[-20:]) / 20 * 100
    print(f"  Ort. gunluk aralik  : %{ar:.2f}  (~{fiyat*ar/100:.2f} TL)")
