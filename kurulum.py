# -*- coding: utf-8 -*-
"""Dort katmanli kurulum tarayicisi.

    1) YON      Haftalik + Gunluk yapi  ->  hangi tarafa bakiyoruz
    2) BOLGE    4H + 1H FVG ve likidite seviyeleri  ->  nerede bekliyoruz
    3) VARIS    fiyat o bolgeye geldi mi  ->  kurulum kuruldu
    4) TETIK    5dk footprint  ->  ne zaman gireriz   (yalniz BTC)

Neden bu sira: stop 5 dakikalik gurultuye degil, 1H/4H yapisina gore
konur. Boylece 1R buyur ve komisyon 1R'nin %88'i yerine %23'u olur.
Footprint burada sinyal DEGIL, zamanlama araci - profesyonel kullanimi
da budur.

NQ ve BIST icin 1-3. katmanlar calisir, 4. katman calismaz: o
enstrumanlarin tik verisi ucretli. Kurulum yine kurulur, sadece
"footprint teyidi yok" diye isaretlenir.

Kullanim:
    python kurulum.py             # 5 enstrumani tara
    python kurulum.py BTCUSDT     # tek enstruman, detayli
"""

import json
import sys
import urllib.request
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
BIST = ("ASELS", "TUPRS", "BIMAS")

# Kurulum kabul esikleri
ASGARI_RR = 2.0          # bunun altinda risk/odul varsa kurulum sayilmaz
BOLGE_PAYI = 0.15        # bolgeye bu kadar ATR yaklasmak "vardi" sayilir
STOP_PAYI = 0.25         # stop, bolgenin bu kadar ATR disina konur

# Iki gurultu korumasi. Bunlar olmadan tarayici sahte firsat uretir:
# 4 puanlik bir FVG, 29 puanlik stop ve 650 puanlik hedefle "R:R 22" gosterir.
# Gercekte o stop ilk dakikada gurultuye takilir - R:R kagit uzerinde kalir.
ASGARI_BOLGE = 0.20      # bolge genisligi en az bu kadar 1H ATR olmali
ASGARI_STOP = 0.50       # stop mesafesi en az bu kadar 1H ATR olmali
AZAMI_RR = 12.0          # bunun ustu neredeyse her zaman olcum hatasidir


def _http(url, timeout=30):
    return json.load(urllib.request.urlopen(
        urllib.request.Request(url, headers=UA), timeout=timeout))


# ---------------------------------------------------------------- veri


def _binance(aralik, limit=300):
    r = _http("https://api.binance.com/api/v3/klines"
              f"?symbol=BTCUSDT&interval={aralik}&limit={limit}")
    return [{"t": int(k[0]) // 1000, "o": float(k[1]), "h": float(k[2]),
             "l": float(k[3]), "c": float(k[4]), "v": float(k[5])} for k in r]


def _yahoo(sembol, aralik, rng):
    d = _http(f"https://query1.finance.yahoo.com/v8/finance/chart/{sembol}"
              f"?interval={aralik}&range={rng}")
    r = d["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    out = []
    for i, t in enumerate(r["timestamp"]):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c):
            continue
        out.append({"t": int(t), "o": o, "h": h, "l": l, "c": c, "v": 0.0})
    return out


def _birlestir(barlar, kat):
    """N bari tek bara toplar (1H -> 4H icin kat=4)."""
    out = []
    for i in range(0, len(barlar) - len(barlar) % kat, kat):
        g = barlar[i:i + kat]
        out.append({"t": g[0]["t"], "o": g[0]["o"],
                    "h": max(x["h"] for x in g), "l": min(x["l"] for x in g),
                    "c": g[-1]["c"], "v": sum(x["v"] for x in g)})
    return out


def veri_cek(ad):
    """Doner: {"1w":[...], "1d":[...], "4h":[...], "1h":[...]}"""
    if ad == "BTCUSDT":
        return {"1w": _binance("1w", 120), "1d": _binance("1d", 300),
                "4h": _binance("4h", 300), "1h": _binance("1h", 400)}
    sembol = "NQ%3DF" if ad == "NQ1!" else f"{ad}.IS"
    saatlik = _yahoo(sembol, "1h", "2y" if ad == "NQ1!" else "730d")
    return {"1w": _yahoo(sembol, "1wk", "5y"),
            "1d": _yahoo(sembol, "1d", "2y"),
            "4h": _birlestir(saatlik, 4),
            "1h": saatlik}


# ---------------------------------------------------------------- yapi


def pivotlar(bars, n=2):
    yuksek, dusuk = [], []
    for i in range(n, len(bars) - n):
        p = bars[i - n:i + n + 1]
        if bars[i]["h"] == max(b["h"] for b in p):
            yuksek.append((i, bars[i]["h"]))
        if bars[i]["l"] == min(b["l"] for b in p):
            dusuk.append((i, bars[i]["l"]))
    return yuksek, dusuk


def yapi_yonu(bars, n=2):
    ph, pl = pivotlar(bars, n)
    if len(ph) < 2 or len(pl) < 2:
        return "RANGE"
    if ph[-1][1] > ph[-2][1] and pl[-1][1] > pl[-2][1]:
        return "BULLISH"
    if ph[-1][1] < ph[-2][1] and pl[-1][1] < pl[-2][1]:
        return "BEARISH"
    return "RANGE"


def atr(bars, n=14):
    if len(bars) < n + 1:
        return None
    t = 0.0
    for i in range(len(bars) - n, len(bars)):
        o = bars[i - 1]["c"]
        t += max(bars[i]["h"] - bars[i]["l"], abs(bars[i]["h"] - o),
                 abs(bars[i]["l"] - o))
    return t / n


# ---------------------------------------------------------------- 1) YON


def yon_belirle(veri):
    """Haftalik + Gunluk. Ikisi ayni yonu gostermiyorsa islem yok."""
    h = yapi_yonu(veri["1w"], n=1)
    g = yapi_yonu(veri["1d"], n=2)
    if h == g and h != "RANGE":
        return h, f"Haftalik {h} + Gunluk {g} — uyumlu"
    if "RANGE" in (h, g) and {h, g} - {"RANGE"}:
        tek = (({h, g}) - {"RANGE"}).pop()
        return tek, f"Haftalik {h} + Gunluk {g} — tek taraf net, zayif"
    return "RANGE", f"Haftalik {h} + Gunluk {g} — uyumsuz, beklenir"


# ---------------------------------------------------------------- 2) BOLGE


def fvg_bul(bars, geri=80):
    """Doldurulmamis Fair Value Gap'ler."""
    out = []
    bas = max(2, len(bars) - geri)
    for i in range(bas, len(bars)):
        c1, c3 = bars[i - 2], bars[i]
        if c1["h"] < c3["l"]:
            out.append({"tip": "bull", "alt": c1["h"], "ust": c3["l"], "i": i})
        elif c1["l"] > c3["h"]:
            out.append({"tip": "bear", "alt": c3["h"], "ust": c1["l"], "i": i})
    canli = []
    for g in out:
        son = bars[g["i"] + 1:]
        if not son:
            canli.append(g)
        elif g["tip"] == "bull" and min(b["l"] for b in son) > g["alt"]:
            canli.append(g)
        elif g["tip"] == "bear" and max(b["h"] for b in son) < g["ust"]:
            canli.append(g)
    return canli


def likidite_seviyeleri(bars, geri=120):
    """Henuz ALINMAMIS swing tepe/dipleri - stop avinin hedefleri."""
    b = bars[-geri:]
    ph, pl = pivotlar(b, n=2)
    fiyat = b[-1]["c"]
    ustler, altlar = [], []
    for i, s in ph:
        if s > fiyat and max(x["h"] for x in b[i + 1:]) < s:
            ustler.append(s)
        elif s < fiyat and max(x["h"] for x in b[i + 1:]) < s:
            altlar.append(s)     # asagida kalmis, alinmamis tepe
    for i, s in pl:
        if s < fiyat and min(x["l"] for x in b[i + 1:]) > s:
            altlar.append(s)
    return sorted(set(ustler)), sorted(set(altlar), reverse=True)


def bolgeler(veri, yon, a1):
    """Yonle uyumlu, fiyata en yakin giris bolgeleri (4H once, sonra 1H).

    Cok DAR bolgeler elenir: genisligi 1H ATR'nin bestesinden kucuk bir FVG
    yapisal bir bolge degil, tek bir mumun kuyrugudur. Ona gore konan stop
    gurultunun icinde kalir.
    """
    fiyat = veri["1h"][-1]["c"]
    tip = "bull" if yon == "BULLISH" else "bear"
    asgari_genislik = a1 * ASGARI_BOLGE
    out = []
    for etiket, bars in (("4H", veri["4h"]), ("1H", veri["1h"])):
        for g in fvg_bul(bars):
            if g["tip"] != tip:
                continue
            if (g["ust"] - g["alt"]) < asgari_genislik:
                continue
            # Long icin bolge fiyatin ALTINDA olmali (dusup gelecek),
            # short icin USTUNDE. Zaten gecilmis bolge kurulum degildir.
            if tip == "bull" and g["ust"] > fiyat * 1.001:
                continue
            if tip == "bear" and g["alt"] < fiyat * 0.999:
                continue
            out.append({"periyot": etiket, "tip": tip,
                        "alt": g["alt"], "ust": g["ust"],
                        "uzaklik": abs(fiyat - (g["alt"] + g["ust"]) / 2)})
    out.sort(key=lambda z: z["uzaklik"])
    return out


# ---------------------------------------------------------------- 3) VARIS


def kurulum_kur(ad, veri):
    """Yon + bolge + hedef birlestirilip somut bir islem plani cikarilir."""
    fiyat = veri["1h"][-1]["c"]
    a1 = atr(veri["1h"])
    yon, yon_sebep = yon_belirle(veri)
    sonuc = {"ad": ad, "fiyat": fiyat, "yon": yon, "yon_sebep": yon_sebep,
             "atr_1h": a1, "durum": "YON YOK", "bolge": None, "plan": None}
    if yon == "RANGE" or not a1:
        return sonuc

    zonlar = bolgeler(veri, yon, a1)
    if not zonlar:
        sonuc["durum"] = "BOLGE YOK (yeterli genislikte)"
        return sonuc

    z = zonlar[0]
    sonuc["bolge"] = z
    ustler, altlar = likidite_seviyeleri(veri["4h"])

    if yon == "BULLISH":
        giris = min(z["ust"], fiyat)
        stop = z["alt"] - a1 * STOP_PAYI
        hedefler = [s for s in ustler if s > giris]
        hedef = hedefler[0] if hedefler else None
    else:
        giris = max(z["alt"], fiyat)
        stop = z["ust"] + a1 * STOP_PAYI
        hedefler = [s for s in altlar if s < giris]
        hedef = hedefler[0] if hedefler else None

    if hedef is None:
        sonuc["durum"] = "HEDEF YOK"
        return sonuc

    # Stop taban kontrolu: yapinin verdigi mesafe 1H gurultusunun altindaysa
    # stop disari itilir. Kagit uzerinde R:R dusurur ama gercekci yapar.
    risk = abs(giris - stop)
    taban = a1 * ASGARI_STOP
    genisletildi = risk < taban
    if genisletildi:
        risk = taban
        stop = giris - risk if yon == "BULLISH" else giris + risk

    odul = abs(hedef - giris)
    rr = odul / risk if risk > 0 else 0
    sonuc["plan"] = {"giris": giris, "stop": stop, "hedef": hedef,
                     "risk": risk, "odul": odul, "rr": rr,
                     "stop_genisletildi": genisletildi}

    if rr < ASGARI_RR:
        sonuc["durum"] = f"RR YETERSIZ ({rr:.1f})"
        return sonuc
    if rr > AZAMI_RR:
        sonuc["durum"] = f"RR SUPHELI ({rr:.1f}) — hedef fazla uzak, elle bak"
        return sonuc

    # Fiyat bolgede mi, yoksa daha gelmedi mi
    pay = a1 * BOLGE_PAYI
    icinde = (z["alt"] - pay) <= fiyat <= (z["ust"] + pay)
    sonuc["durum"] = "BOLGEDE — TETIK BEKLENIYOR" if icinde else "BEKLEMEDE"
    return sonuc


# ---------------------------------------------------------------- 4) TETIK


def footprint_tetik(yon, dakika=90):
    """5dk footprint'te giris tetigi. Yalniz BTC.

    LONG icin aranan: satis tukenmesi.
      - satis emildi (saticilar agresifti, fiyat dusmedi), veya
      - delta sert negatif ama bar govdesi yukari kapandi
    SHORT icin aynisinin aynasi.
    """
    try:
        import footprint as FP
    except Exception:
        return None
    isl = FP.canli_pencere("BTCUSDT", dakika=dakika)
    if not isl:
        return None
    barlar = FP.footprint_barlar(isl, 300)
    if len(barlar) < 3:
        return None

    tetikler = []
    for b in barlar[-6:]:
        o = FP.ozet(b)
        aralik = b["h"] - b["l"]
        konum = (b["c"] - b["l"]) / aralik if aralik > 0 else 0.5
        if yon == "BULLISH":
            if o["emilim"] == "satis_emildi":
                tetikler.append((o["zaman"], "satis emildi", o["delta_orani"]))
            elif o["delta_orani"] < -0.30 and konum > 0.60:
                tetikler.append((o["zaman"], "agresif satis emiliyor",
                                 o["delta_orani"]))
        else:
            if o["emilim"] == "alis_emildi":
                tetikler.append((o["zaman"], "alis emildi", o["delta_orani"]))
            elif o["delta_orani"] > 0.30 and konum < 0.40:
                tetikler.append((o["zaman"], "agresif alis emiliyor",
                                 o["delta_orani"]))
    son = FP.ozet(barlar[-1])
    return {"tetikler": tetikler, "son_bar": son,
            "cvd_yon": "yukari" if sum(b["delta"] for b in barlar[-6:]) > 0
                       else "asagi"}


# ---------------------------------------------------------------- rapor


def f(x, ond=2):
    return "-" if x is None else f"{x:,.{ond}f}"


def yazdir(s, tetik=None):
    print(f"\n{'=' * 70}")
    print(f"{s['ad']}   fiyat {f(s['fiyat'])}   —   {s['durum']}")
    print(f"{'=' * 70}")
    print(f"1) YON    : {s['yon']}")
    print(f"           {s['yon_sebep']}")
    z = s["bolge"]
    if z:
        print(f"2) BOLGE  : {z['periyot']} {z['tip']} FVG   "
              f"{f(z['alt'])} — {f(z['ust'])}")
    else:
        print("2) BOLGE  : yonle uyumlu, fiyatin onunde bolge yok")
    p = s["plan"]
    if p:
        print(f"3) PLAN   : giris {f(p['giris'])}   stop {f(p['stop'])}   "
              f"hedef {f(p['hedef'])}")
        print(f"           risk {f(p['risk'])}   odul {f(p['odul'])}   "
              f"R:R = {p['rr']:.1f}"
              + ("   (stop gurultu tabanina genisletildi)"
                 if p.get('stop_genisletildi') else ""))
    if tetik is not None:
        print(f"4) TETIK  : son 30dk footprint")
        if tetik["tetikler"]:
            for zaman, sebep, do in tetik["tetikler"]:
                print(f"           ✅ {zaman}  {sebep}  (delta {do:+.0%})")
        else:
            print("           tetik yok — bolgedeyiz ama order flow donmedi")
        sb = tetik["son_bar"]
        print(f"           son bar delta {sb['delta_orani']:+.0%}  "
              f"POC {sb['poc']}  CVD {tetik['cvd_yon']}")
    elif s["ad"] != "BTCUSDT":
        print("4) TETIK  : yok — bu enstrumanda tik verisi ucretli, "
              "footprint kurulamiyor")


def main():
    hedefler = [sys.argv[1]] if len(sys.argv) > 1 else \
        ["BTCUSDT", "NQ1!", "ASELS", "TUPRS", "BIMAS"]
    print(f"Kurulum taramasi — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC")
    print(f"Kabul esigi: R:R >= {ASGARI_RR}")
    for ad in hedefler:
        try:
            veri = veri_cek(ad)
        except Exception as ex:
            print(f"\n{ad}: veri alinamadi ({type(ex).__name__}: {ex})")
            continue
        s = kurulum_kur(ad, veri)
        tetik = None
        if ad == "BTCUSDT" and s["durum"].startswith("BOLGEDE"):
            tetik = footprint_tetik(s["yon"])
        yazdir(s, tetik)
    return 0


if __name__ == "__main__":
    sys.exit(main())
