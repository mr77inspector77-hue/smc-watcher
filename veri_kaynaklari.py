# -*- coding: utf-8 -*-
"""Coklu veri kaynagi katmani.

Her enstruman icin sirali kaynak listesi. Ilk TAZE donen kullanilir.
Hicbiri taze degilse (None, ...) doner -> cagiran taraf "kor" muamelesi yapar.

Hicbir kaynak API anahtari gerektirmez.
"""

import json
import urllib.request
from datetime import datetime, timezone

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _http(url, timeout=25):
    return json.load(urllib.request.urlopen(
        urllib.request.Request(url, headers=UA), timeout=timeout))


def yas_dk(bars):
    if not bars:
        return 9e9
    return (datetime.now(timezone.utc).timestamp() - bars[-1]["t"]) / 60.0


# ------------------------------------------------------------------ BTC


def binance_15m(sembol="BTCUSDT", limit=300):
    raw = _http("https://api.binance.com/api/v3/klines"
                f"?symbol={sembol}&interval=15m&limit={limit}")
    return [{"t": int(k[0]) // 1000, "o": float(k[1]), "h": float(k[2]),
             "l": float(k[3]), "c": float(k[4]), "v": float(k[5])} for k in raw]


def coinbase_15m(urun="BTC-USD"):
    # [time, low, high, open, close, volume] - yeniden eskiye
    raw = _http(f"https://api.exchange.coinbase.com/products/{urun}"
                "/candles?granularity=900")
    bars = [{"t": int(k[0]), "o": float(k[3]), "h": float(k[2]),
             "l": float(k[1]), "c": float(k[4]), "v": float(k[5])} for k in raw]
    return sorted(bars, key=lambda b: b["t"])


def kraken_15m(cift="XBTUSDT"):
    d = _http(f"https://api.kraken.com/0/public/OHLC?pair={cift}&interval=15")
    if d.get("error"):
        raise RuntimeError(str(d["error"]))
    anahtar = [k for k in d["result"] if k != "last"][0]
    return [{"t": int(k[0]), "o": float(k[1]), "h": float(k[2]),
             "l": float(k[3]), "c": float(k[4]), "v": float(k[6])}
            for k in d["result"][anahtar]]


# ------------------------------------------------------------------ Yahoo


def yahoo_15m(sembol, rng="5d", sunucu=1, aralik="15m", prepost=False):
    ek = "&includePrePost=true" if prepost else ""
    d = _http(f"https://query{sunucu}.finance.yahoo.com/v8/finance/chart/{sembol}"
              f"?interval={aralik}&range={rng}{ek}")
    r = d["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    hac = q.get("volume") or [0] * len(r["timestamp"])
    out = []
    for i, t in enumerate(r["timestamp"]):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c):
            continue
        out.append({"t": int(t), "o": o, "h": h, "l": l, "c": c,
                    "v": float(hac[i] or 0)})
    return out


# ------------------------------------------------------------------ kayit

# (etiket, fonksiyon, vekil_mi)
KAYNAKLAR = {
    "BTCUSDT": [
        ("Binance",  lambda: binance_15m("BTCUSDT"), False),
        ("Coinbase", lambda: coinbase_15m("BTC-USD"), False),
        ("Kraken",   lambda: kraken_15m("XBTUSDT"), False),
    ],
    "NQ1!": [
        # MNQ ayni endeks degerini kotar (fark ~0.25 puan) -> vekil sayilmaz,
        # ayrica 24 saat acik. QQQ son care: ETF, farkli olcek -> vekil.
        ("Yahoo NQ=F",    lambda: yahoo_15m("NQ%3DF"), False),
        ("Yahoo NQ=F q2", lambda: yahoo_15m("NQ%3DF", sunucu=2), False),
        ("Yahoo MNQ=F",   lambda: yahoo_15m("MNQ%3DF"), False),
        ("Yahoo MNQ=F q2", lambda: yahoo_15m("MNQ%3DF", sunucu=2), False),
        ("QQQ (vekil)",   lambda: yahoo_15m("QQQ", prepost=True), True),
    ],
}

for _kod in ("ASELS", "TUPRS", "BIMAS"):
    KAYNAKLAR[_kod] = [
        (f"Yahoo {_kod}.IS",    (lambda k: lambda: yahoo_15m(f"{k}.IS"))(_kod), False),
        (f"Yahoo {_kod}.IS q2", (lambda k: lambda: yahoo_15m(f"{k}.IS", sunucu=2))(_kod), False),
    ]


# ------------------------------------------------------------------ HTF
#
# Ust periyot (haftalik/gunluk/4H/1H). Yon, bolge ve plan buradan kurulur.
#
# NEDEN AYRI BIR YEDEK ZINCIRI: GitHub Actions kosucularindan
# api.binance.com'a erisilemiyor (kayitlarda 10/10 kosuda BTC kaynagi
# Coinbase'e dustu, yerelde her seferinde Binance secildi). 15dk verisi
# icin zaten yedek vardi; ust periyot tek kaynaga bagli kalirsa BTC
# bulutta tamamen korlesir.


def _birlestir(barlar, kova_sn, kaydirma=0):
    """Barlari TAKVIME hizali kovalara toplar (indeksle dilimlemez).

    Sabit N'lik dilimleme, serinin nereden basladigina gore haftayi carsamba
    gunune oturtabiliyor - haftalik pivotlar kayinca yon okumasi degisiyordu
    (olculdu: turetilmis haftalikta BEARISH/uyumlu, natifte BEARISH/zayif).
    Kova sinirini zaman damgasindan hesaplamak bu kaymayi kaldirir.

    kaydirma: epoch persembeye denk gelir; haftalik kovayi pazartesi
    00:00 UTC'ye oturtmak icin 4 gun kaydirilir.
    """
    kovalar = {}
    for b in barlar:
        k = (b["t"] + kaydirma) // kova_sn
        g = kovalar.get(k)
        if g is None:
            kovalar[k] = {"t": b["t"], "o": b["o"], "h": b["h"], "l": b["l"],
                          "c": b["c"], "v": b["v"]}
        else:
            g["h"] = max(g["h"], b["h"])
            g["l"] = min(g["l"], b["l"])
            g["c"] = b["c"]
            g["v"] += b["v"]
    return [kovalar[k] for k in sorted(kovalar)]


def binance_htf(sembol="BTCUSDT"):
    def k(aralik, limit):
        raw = _http("https://api.binance.com/api/v3/klines"
                    f"?symbol={sembol}&interval={aralik}&limit={limit}")
        return [{"t": int(x[0]) // 1000, "o": float(x[1]), "h": float(x[2]),
                 "l": float(x[3]), "c": float(x[4]), "v": float(x[5])}
                for x in raw]
    return {"1w": k("1w", 120), "1d": k("1d", 300),
            "4h": k("4h", 300), "1h": k("1h", 400)}


def kraken_htf(cift="XBTUSDT"):
    """Kraken dort periyodu da NATIF verir - turetme yok, en iyi yedek."""
    def k(dakika):
        d = _http("https://api.kraken.com/0/public/OHLC"
                  f"?pair={cift}&interval={dakika}")
        if d.get("error"):
            raise RuntimeError(str(d["error"]))
        anahtar = [x for x in d["result"] if x != "last"][0]
        return [{"t": int(x[0]), "o": float(x[1]), "h": float(x[2]),
                 "l": float(x[3]), "c": float(x[4]), "v": float(x[6])}
                for x in d["result"][anahtar]]
    return {"1w": k(10080), "1d": k(1440), "4h": k(240), "1h": k(60)}


def coinbase_htf(urun="BTC-USD"):
    """Coinbase 4H ve 1W vermez; 1H'den 4H, 1G'den 1W turetilir."""
    def k(gran):
        raw = _http("https://api.exchange.coinbase.com/products/"
                    f"{urun}/candles?granularity={gran}")
        b = [{"t": int(x[0]), "o": float(x[3]), "h": float(x[2]),
              "l": float(x[1]), "c": float(x[4]), "v": float(x[5])}
             for x in raw]
        return sorted(b, key=lambda z: z["t"])
    saat, gun = k(3600), k(86400)
    return {"1w": _birlestir(gun, 604800, kaydirma=345600), "1d": gun,
            "4h": _birlestir(saat, 14400), "1h": saat}


HTF_KAYNAKLAR = {
    "BTCUSDT": [("Binance", binance_htf),
                ("Kraken", kraken_htf),
                ("Coinbase", coinbase_htf)],
}

# Yapi okunabilmesi icin gereken asgari bar sayisi. Bunun altinda kaynak
# "cevap verdi" sayilmaz - yarim seriyle kurulan yon yanlis yondur.
ASGARI_BAR = {"1w": 12, "1d": 60, "4h": 60, "1h": 60}


def htf_cek(ad, log=None):
    """Ust periyot serileri. Ilk YETERLI donen kaynak kullanilir.

    Doner: (veri, etiket). Hicbiri yetmezse RuntimeError - cagiran taraf
    bunu "bu enstrumanda korum" diye bildirir, sessizce atlamaz.
    """
    hatalar = []
    for etiket, fn in HTF_KAYNAKLAR.get(ad, []):
        try:
            v = fn()
            eksik = [p for p, n in ASGARI_BAR.items() if len(v.get(p, [])) < n]
            if eksik:
                hatalar.append(f"{etiket}=eksik({','.join(eksik)})")
                continue
            if log:
                log(f"{ad}: HTF kaynak={etiket}")
            return v, etiket
        except Exception as ex:
            hatalar.append(f"{etiket}=HATA({type(ex).__name__})")
    raise RuntimeError(f"{ad}: ust periyot kaynagi yok -> "
                       + ", ".join(hatalar or ["kayitli kaynak yok"]))


def bar_cek(ad, azami_yas, log=None):
    """Sirali kaynaklari dener. Donen: (bars, etiket, vekil_mi, yas, denemeler)

    bars None ise hicbir kaynak taze veri veremedi.
    """
    denemeler = []
    en_iyi = None  # bayat da olsa elimizdeki en taze veri

    for etiket, fn, vekil in KAYNAKLAR.get(ad, []):
        try:
            bars = fn()
            y = yas_dk(bars)
            denemeler.append(f"{etiket}={y:.0f}dk")
            if en_iyi is None or y < en_iyi[3]:
                en_iyi = (bars, etiket, vekil, y)
            if y <= azami_yas and len(bars) >= 60:
                if log:
                    log(f"{ad}: kaynak={etiket} yas={y:.0f}dk"
                        + ("  [VEKIL]" if vekil else ""))
                return bars, etiket, vekil, y, denemeler
        except Exception as ex:
            denemeler.append(f"{etiket}=HATA({type(ex).__name__})")

    if log:
        log(f"{ad}: TAZE KAYNAK YOK -> {', '.join(denemeler)}")
    return None, (en_iyi[1] if en_iyi else "-"), False, \
        (en_iyi[3] if en_iyi else 9e9), denemeler
