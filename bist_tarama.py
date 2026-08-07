# -*- coding: utf-8 -*-
"""BIST100 - SMC ile trade edilebilirlik taramasi.

Olcut: likidite + gunluk hareket alani + GRAFIK TEMIZLIGI.
"Saçmalamayan grafik" = az gap, az tavan/taban, tutarli volatilite,
govdesi fitilinden buyuk mumlar.
"""
import json, urllib.request, statistics as st

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

ADAYLAR = {
    "GARAN": "Bankacilik", "AKBNK": "Bankacilik", "YKBNK": "Bankacilik",
    "ISCTR": "Bankacilik", "VAKBN": "Bankacilik", "HALKB": "Bankacilik",
    "KCHOL": "Holding", "SAHOL": "Holding",
    "ASELS": "Savunma", "OTKAR": "Savunma",
    "EREGL": "Demir-Celik", "KRDMD": "Demir-Celik",
    "THYAO": "Havacilik", "PGSUS": "Havacilik",
    "BIMAS": "Perakende", "MGROS": "Perakende", "SOKM": "Perakende",
    "TUPRS": "Rafineri", "PETKM": "Petrokimya",
    "FROTO": "Otomotiv", "TOASO": "Otomotiv",
    "TCELL": "Telekom", "TTKOM": "Telekom",
    "SISE": "Cam", "OYAKC": "Cimento",
    "ENKAI": "Insaat", "AKSEN": "Enerji", "ZOREN": "Enerji",
    "AEFES": "Icecek", "CCOLA": "Icecek", "ULKER": "Gida",
    "KOZAL": "Madencilik", "ARCLK": "Beyaz Esya", "HEKTS": "Kimya",
    "LOGO": "Teknoloji",
}


def cek(kod, rng="3mo"):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{kod}.IS"
           f"?interval=1d&range={rng}")
    req = urllib.request.Request(url, headers=UA)
    d = json.load(urllib.request.urlopen(req, timeout=25))
    r = d["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    bars = []
    for i in range(len(r["timestamp"])):
        o, h, l, c, v = q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i]
        if None in (o, h, l, c) or not v:
            continue
        bars.append({"o": o, "h": h, "l": l, "c": c, "v": v})
    return bars


def analiz(kod, bars):
    n = len(bars)
    if n < 40:
        return None
    kapanislar = [b["c"] for b in bars]
    fiyat = kapanislar[-1]

    # 1) Likidite: ortalama gunluk islem hacmi (milyon TL)
    hacim_tl = st.mean(b["v"] * b["c"] for b in bars) / 1_000_000

    # 2) Gunluk hareket alani %
    aralik = [(b["h"] - b["l"]) / b["c"] * 100 for b in bars]
    ort_aralik = st.mean(aralik)

    # 3) Volatilite tutarliligi (dusuk = ongorulebilir)
    tutarlilik = st.pstdev(aralik) / ort_aralik if ort_aralik else 9

    # 4) Gap sikligi: acilisin onceki kapanistan %1'den fazla sapmasi
    gapler = sum(1 for i in range(1, n)
                 if abs(bars[i]["o"] - bars[i - 1]["c"]) / bars[i - 1]["c"] > 0.01)
    gap_yuzde = gapler / (n - 1) * 100

    # 5) Tavan/taban gunleri (|degisim| >= %9)
    limit = sum(1 for i in range(1, n)
                if abs(bars[i]["c"] - bars[i - 1]["c"]) / bars[i - 1]["c"] >= 0.09)
    limit_yuzde = limit / (n - 1) * 100

    # 6) Govde / toplam mum orani (yuksek = temiz yonlu mumlar)
    govde = st.mean(abs(b["c"] - b["o"]) / (b["h"] - b["l"]) if b["h"] > b["l"] else 0
                    for b in bars) * 100

    # ---- puanlama (100 uzerinden) ----
    # Likidite ana kriter: 6 mia TL gunluk hacim = tam puan (log olcek)
    import math
    p_likidite = min(math.log10(max(hacim_tl, 1)) / math.log10(6000), 1) * 40
    p_aralik = max(0, 1 - abs(ort_aralik - 3.5) / 3.5) * 15   # ideal ~%3.5
    p_tutarli = max(0, 1 - tutarlilik / 0.8) * 15
    p_gap = max(0, 1 - gap_yuzde / 45) * 12
    p_limit = max(0, 1 - limit_yuzde / 8) * 10
    p_govde = min(govde / 55, 1) * 8
    puan = p_likidite + p_aralik + p_tutarli + p_gap + p_limit + p_govde

    return {
        "kod": kod, "sektor": ADAYLAR[kod], "fiyat": fiyat,
        "hacim_mtl": hacim_tl, "aralik": ort_aralik, "tutarlilik": tutarlilik,
        "gap": gap_yuzde, "limit": limit_yuzde, "govde": govde,
        "puan": puan, "bar": n,
    }


sonuc = []
for kod in ADAYLAR:
    try:
        r = analiz(kod, cek(kod))
        if r:
            sonuc.append(r)
    except Exception as e:
        print(f"  ! {kod}: {e}")

sonuc.sort(key=lambda x: -x["puan"])
print(f"\n{'Kod':<7}{'Sektor':<14}{'Fiyat':>9}{'HacimMTL':>10}{'Aralik%':>9}"
      f"{'Tutar':>7}{'Gap%':>7}{'Lmt%':>6}{'Govde%':>8}{'PUAN':>7}")
print("-" * 84)
for r in sonuc:
    print(f"{r['kod']:<7}{r['sektor']:<14}{r['fiyat']:>9.2f}{r['hacim_mtl']:>10.0f}"
          f"{r['aralik']:>9.2f}{r['tutarlilik']:>7.2f}{r['gap']:>7.1f}"
          f"{r['limit']:>6.1f}{r['govde']:>8.1f}{r['puan']:>7.1f}")
