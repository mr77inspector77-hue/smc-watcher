# -*- coding: utf-8 -*-
"""Footprint (order flow) veri katmani.

OHLCV bir barin NE KADAR hareket ettigini soyler; footprint barin ICINDE
hangi fiyatta kimin agresif oldugunu soyler. Bunun icin tik seviyesinde
islem verisi ve her islemin AGRESOR TARAFI gerekir - OHLCV'den turetilemez.

Kaynak: Binance aggTrades. `m` alani "alici maker miydi" demektir:
    m=False -> agresor ALICI  -> islem ASK tarafinda  (yesil)
    m=True  -> agresor SATICI -> islem BID tarafinda  (kirmizi)

Iki mod:
  canli_pencere() : son N dakikanin islemleri (REST, sayfalanir)
  arsiv_gun()     : data.binance.vision gunluk ZIP (geri test icin)

NOT: Bu modul CANLI SINYAL URETMEZ. Once olculur, sonra karar verilir.
"""

import csv
import io
import json
import os
import time
import urllib.request
import zipfile
from collections import defaultdict
from datetime import datetime, timezone

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
ARSIV = "https://data.binance.vision/data/spot/daily/aggTrades"

# Diagonal dengesizlik esigi: bir tarafin capraz komsusunun kac kati olmasi
# gerektigi. Footprint literaturunde 3 standarttir.
DENGESIZLIK_KAT = 3.0
# Gurultu esigi. MUTLAK bir sayi olamaz: BTC'de 0.1 coin anlamli, baska
# enstrumanda anlamsizdir. Barin toplam hacminin oranı olarak alinir.
DENGESIZLIK_ASGARI_ORAN = 0.005     # barin hacminin %0.5'i


def _http(url, timeout=30):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=UA), timeout=timeout)


# ---------------------------------------------------------------- veri cekme


def _zaman_sn(t):
    """Binance bazi serilerde milisaniye, bazilarinda mikrosaniye doner.
    Buyuklukten ayirt et - sabit varsaymak barlari 1000 kat kaydirir."""
    t = int(t)
    if t > 1e15:          # mikrosaniye
        return t / 1e6
    if t > 1e12:          # milisaniye
        return t / 1e3
    return float(t)       # saniye


def canli_pencere(sembol="BTCUSDT", dakika=20, log=None):
    """Son `dakika` dakikanin islemleri. Sayfalanir (limit 1000).

    Doner: [{"t": saniye(float), "p": fiyat, "q": miktar, "alici": bool}, ...]
    """
    bit = int(time.time() * 1000)
    bas = bit - dakika * 60 * 1000
    cikti, imlec, sayfa = [], bas, 0
    while imlec < bit and sayfa < 40:
        url = (f"https://api.binance.com/api/v3/aggTrades?symbol={sembol}"
               f"&startTime={imlec}&endTime={min(imlec + 3600000, bit)}&limit=1000")
        try:
            ham = json.load(_http(url))
        except Exception as ex:
            if log:
                log(f"footprint: sayfa alinamadi ({type(ex).__name__})")
            break
        if not ham:
            break
        for k in ham:
            cikti.append({"t": _zaman_sn(k["T"]), "p": float(k["p"]),
                          "q": float(k["q"]), "alici": not k["m"]})
        yeni = int(ham[-1]["T"]) + 1
        if yeni <= imlec:
            break
        imlec = yeni
        sayfa += 1
        if len(ham) < 1000:
            break
    return cikti


def arsiv_gun(sembol, tarih, log=None):
    """data.binance.vision gunluk aggTrades ZIP'i. Geri test icindir.

    tarih: "YYYY-MM-DD"
    """
    url = f"{ARSIV}/{sembol}/{sembol}-aggTrades-{tarih}.zip"
    # ZIP'i yerelde tut: ayni gunu farkli periyotlarda (5dk/15dk) islerken
    # yuzlerce MB'i tekrar indirmek anlamsiz.
    kls = os.path.join(os.path.dirname(os.path.abspath(__file__)), "footprint_ham")
    os.makedirs(kls, exist_ok=True)
    yerel = os.path.join(kls, f"{sembol}-{tarih}.zip")
    if os.path.exists(yerel):
        ham = open(yerel, "rb").read()
    else:
        try:
            ham = _http(url, timeout=180).read()
            open(yerel, "wb").write(ham)
        except Exception as ex:
            if log:
                log(f"footprint arsiv yok: {tarih} ({type(ex).__name__})")
            return []
    cikti = []
    with zipfile.ZipFile(io.BytesIO(ham)) as z:
        with z.open(z.namelist()[0]) as f:
            for satir in csv.reader(io.TextIOWrapper(f, encoding="utf-8")):
                if not satir or not satir[1].replace(".", "").isdigit():
                    continue        # baslik satiri
                # agg_id, fiyat, miktar, ilk_id, son_id, zaman, alici_maker, ...
                cikti.append({"t": _zaman_sn(satir[5]), "p": float(satir[1]),
                              "q": float(satir[2]),
                              "alici": satir[6].strip().lower() in ("false", "0")})
    return cikti


# ---------------------------------------------------------------- bar kurma


def kademe_sec(fiyat):
    """Fiyat buyuklugune gore makul bir fiyat kovasi secer.

    Cok ince kademe seviyeleri dagitir (her seviyede 1 islem, desen gorunmez),
    cok kalin kademe bilgiyi ezer. Hedef, 15dk barin 20-50 seviyeye
    bolunmesi - footprint grafiklerinde okunabilir aralik budur.
    """
    for esik, kademe in ((50000, 5.0), (10000, 1.0), (1000, 0.5),
                         (100, 0.05), (10, 0.01)):
        if fiyat >= esik:
            return kademe
    return 0.001


def footprint_barlar(islemler, periyot_sn=900, kademe=None):
    """Islemleri footprint barlarina toplar.

    Her bar:
      t, o, h, l, c, hacim
      seviyeler : {fiyat_kovasi: [bid_hacim, ask_hacim]}
      delta     : ask_hacim - bid_hacim  (agresif alim - agresif satim)
      poc       : en cok hacim goren fiyat kovasi
    """
    if not islemler:
        return []
    if kademe is None:
        kademe = kademe_sec(islemler[0]["p"])

    kovalar = defaultdict(lambda: {"seviyeler": defaultdict(lambda: [0.0, 0.0]),
                                   "ilk": None, "son": None,
                                   "h": -1e18, "l": 1e18})
    for i in islemler:
        k = int(i["t"] // periyot_sn) * periyot_sn
        b = kovalar[k]
        f = round(round(i["p"] / kademe) * kademe, 8)
        b["seviyeler"][f][1 if i["alici"] else 0] += i["q"]
        if b["ilk"] is None:
            b["ilk"] = i["p"]
        b["son"] = i["p"]
        b["h"] = max(b["h"], i["p"])
        b["l"] = min(b["l"], i["p"])

    barlar = []
    for k in sorted(kovalar):
        b = kovalar[k]
        sev = {f: v for f, v in b["seviyeler"].items()}
        bid = sum(v[0] for v in sev.values())
        ask = sum(v[1] for v in sev.values())
        poc = max(sev, key=lambda f: sev[f][0] + sev[f][1])
        barlar.append({
            "t": k, "o": b["ilk"], "h": b["h"], "l": b["l"], "c": b["son"],
            "hacim": bid + ask, "bid": bid, "ask": ask, "delta": ask - bid,
            "poc": poc, "kademe": kademe, "seviyeler": sev,
        })
    return barlar


# ---------------------------------------------------------------- metrikler


def dengesizlikler(bar, kat=DENGESIZLIK_KAT, asgari_oran=DENGESIZLIK_ASGARI_ORAN):
    """Diagonal (capraz) dengesizlikler.

    Footprint'te ask hacmi BIR ALT seviyedeki bid hacmiyle karsilastirilir -
    cunku ayni fiyatta alici ve satici zaten esittir; asil bilgi capraz
    kiyastadir.

    Gurultu korumasi: capraz komsusu SIFIR olan her seviye teknik olarak
    "sonsuz kat" dengesizliktir. Bar hacminin belli bir orani altindaki
    seviyeler sayilmazsa footprint'in kenarlari sahte dengesizlikle dolar.

    Doner: {"alis": [fiyat,...], "satis": [fiyat,...]}
    """
    sev = bar["seviyeler"]
    kademe = bar["kademe"]
    asgari = bar["hacim"] * asgari_oran
    alis, satis = [], []
    for f, (bid, ask) in sev.items():
        alt = sev.get(round(f - kademe, 8))
        ust = sev.get(round(f + kademe, 8))
        if alt is not None and ask >= asgari and ask >= alt[0] * kat:
            alis.append(f)
        if ust is not None and bid >= asgari and bid >= ust[1] * kat:
            satis.append(f)
    return {"alis": sorted(alis), "satis": sorted(satis)}


def deger_alani(bar, oran=0.70):
    """POC'tan disa dogru buyuyerek hacmin `oran` kadarini kapsayan aralik."""
    sev = bar["seviyeler"]
    if not sev:
        return None, None
    toplam = sum(v[0] + v[1] for v in sev.values())
    hedef = toplam * oran
    sirali = sorted(sev)
    i = sirali.index(bar["poc"])
    alt = ust = i
    birikmis = sum(sev[sirali[i]])
    while birikmis < hedef and (alt > 0 or ust < len(sirali) - 1):
        asagi = sum(sev[sirali[alt - 1]]) if alt > 0 else -1
        yukari = sum(sev[sirali[ust + 1]]) if ust < len(sirali) - 1 else -1
        if yukari >= asagi:
            ust += 1
            birikmis += yukari
        else:
            alt -= 1
            birikmis += asagi
    return sirali[alt], sirali[ust]


def emilim(bar, delta_orani=0.15, uc_orani=0.35):
    """Emilim (absorption): guclu delta, zayif fiyat tepkisi.

    Agresif taraf bariz baskin ama bar o yone kapanmiyorsa, karsi tarafta
    limit emirler agresyonu emiyor demektir. Klasik donus adayi.

    Doner: None | "alis_emildi" | "satis_emildi"
    """
    aralik = bar["h"] - bar["l"]
    if aralik <= 0 or bar["hacim"] <= 0:
        return None
    d = bar["delta"] / bar["hacim"]
    kapanis_konum = (bar["c"] - bar["l"]) / aralik
    if d >= delta_orani and kapanis_konum <= uc_orani:
        return "alis_emildi"       # alicilar agresifti, fiyat dipte kapandi
    if d <= -delta_orani and kapanis_konum >= 1 - uc_orani:
        return "satis_emildi"      # saticilar agresifti, fiyat tepede kapandi
    return None


def ozet(bar):
    """Bir footprint barinin tasinabilir ozeti (seviye sozlugu haric)."""
    va_alt, va_ust = deger_alani(bar)
    dz = dengesizlikler(bar)
    return {
        "t": bar["t"],
        "zaman": datetime.fromtimestamp(bar["t"], timezone.utc)
                         .strftime("%Y-%m-%d %H:%M UTC"),
        "o": bar["o"], "h": bar["h"], "l": bar["l"], "c": bar["c"],
        "hacim": round(bar["hacim"], 4),
        "delta": round(bar["delta"], 4),
        "delta_orani": round(bar["delta"] / bar["hacim"], 4) if bar["hacim"] else 0,
        "poc": bar["poc"],
        "va_alt": va_alt, "va_ust": va_ust,
        "seviye_sayisi": len(bar["seviyeler"]),
        "dengesizlik_alis": len(dz["alis"]),
        "dengesizlik_satis": len(dz["satis"]),
        "emilim": emilim(bar),
    }


def kumulatif_delta(barlar):
    """CVD - kumulatif delta serisi."""
    toplam, seri = 0.0, []
    for b in barlar:
        toplam += b["delta"]
        seri.append(toplam)
    return seri


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("Son 60 dakika BTCUSDT footprint (15dk bar):\n")
    isl = canli_pencere("BTCUSDT", dakika=60)
    print(f"{len(isl)} islem cekildi\n")
    barlar = footprint_barlar(isl, 900)
    for b in barlar:
        o = ozet(b)
        print(f"{o['zaman']}  O{o['o']:.0f} H{o['h']:.0f} L{o['l']:.0f} C{o['c']:.0f}")
        print(f"   hacim {o['hacim']:>10.3f}   delta {o['delta']:>+9.3f} "
              f"({o['delta_orani']:+.1%})   POC {o['poc']}")
        print(f"   deger alani {o['va_alt']} - {o['va_ust']}   "
              f"{o['seviye_sayisi']} seviye   "
              f"dengesizlik A{o['dengesizlik_alis']}/S{o['dengesizlik_satis']}"
              + (f"   EMILIM: {o['emilim']}" if o["emilim"] else ""))
