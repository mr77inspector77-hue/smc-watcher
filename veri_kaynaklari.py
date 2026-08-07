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


# ------------------------------------------------------------------ gunluk

# Daily/Weekly bias icin gunluk bar. Tazelik kritik degil - bias gun icinde
# degismez - o yuzden tek kaynak yeter, hata halinde sessizce None doner.

def binance_gunluk(sembol="BTCUSDT", limit=200):
    raw = _http("https://api.binance.com/api/v3/klines"
                f"?symbol={sembol}&interval=1d&limit={limit}")
    return [{"t": int(k[0]) // 1000, "o": float(k[1]), "h": float(k[2]),
             "l": float(k[3]), "c": float(k[4]), "v": float(k[5])} for k in raw]


GUNLUK = {
    "BTCUSDT": [lambda: binance_gunluk("BTCUSDT"),
                lambda: yahoo_15m("BTC-USD", rng="1y", aralik="1d")],
    "NQ1!":    [lambda: yahoo_15m("NQ%3DF", rng="1y", aralik="1d"),
                lambda: yahoo_15m("QQQ", rng="1y", aralik="1d")],
}
for _kod in ("ASELS", "TUPRS", "BIMAS"):
    GUNLUK[_kod] = [(lambda k: lambda: yahoo_15m(f"{k}.IS", rng="1y",
                                                 aralik="1d"))(_kod)]


def gunluk_cek(ad, log=None):
    for fn in GUNLUK.get(ad, []):
        try:
            bars = fn()
            if len(bars) >= 30:
                return bars
        except Exception as ex:
            if log:
                log(f"{ad}: gunluk bar alinamadi ({type(ex).__name__})")
    return None


# ------------------------------------------------------------------ korelasyon

# SMT icin korele enstruman. Sira onemli: ilk calisan kullanilir.
# NQ<->ES en guclu cift. BIST hissesi <-> endeks ciftinin korelasyonu daha
# zayiftir; mesajda bu acikca belirtilir.
KORELE = {
    "BTCUSDT": [("ETH", lambda: binance_15m("ETHUSDT")),
                ("ETH", lambda: coinbase_15m("ETH-USD"))],
    "NQ1!":    [("ES (S&P vadeli)", lambda: yahoo_15m("ES%3DF")),
                ("YM (Dow vadeli)", lambda: yahoo_15m("YM%3DF"))],
}
for _kod in ("ASELS", "TUPRS", "BIMAS"):
    KORELE[_kod] = [("XU100", lambda: yahoo_15m("XU100.IS")),
                    ("XU030", lambda: yahoo_15m("XU030.IS"))]


def korele_cek(ad, azami_yas=90, log=None):
    """SMT icin korele enstrumanin barlari. Bulunamazsa (None, None) -
    SMT opsiyoneldir, yoklugu takibi durdurmaz."""
    for etiket, fn in KORELE.get(ad, []):
        try:
            bars = fn()
            if len(bars) >= 40 and yas_dk(bars) <= azami_yas:
                return bars, etiket
        except Exception as ex:
            if log:
                log(f"{ad}: korele {etiket} alinamadi ({type(ex).__name__})")
    return None, None


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
