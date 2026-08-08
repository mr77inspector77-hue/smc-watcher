# -*- coding: utf-8 -*-
"""BIST hisseleri icin SMC kurulum motoru. Periyotlar: 1H / 4H / Gunluk.

    1) YON     Gunluk yapi yonu belirler, 4H teyit eder
    2) BOLGE   4H + 1H FVG ve likidite seviyeleri -> nerede bekliyoruz
    3) VARIS   fiyat o bolgeye geldi mi -> kurulum kuruldu

BIST YALNIZ LONG. Aсagi yon cikarsa kurulum kurulmaz: aciga satis izne
bagli, yukari adim kurali var ve tek yonlu bir piyasada short'un beklenen
degeri negatif.

Hisse listesi smc_watch.py icinde TAKIP_LISTESI sabitindedir.

Kullanim:
    python kurulum.py             # takip listesini tara
    python kurulum.py ASELS       # tek hisse
"""

import json
import sys
import urllib.request
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# Kurulum kabul esikleri
ASGARI_RR = 2.0          # bunun altinda risk/odul varsa kurulum sayilmaz
BOLGE_PAYI = 0.15        # bolgeye bu kadar ATR yaklasmak "vardi" sayilir
STOP_PAYI = 0.25         # stop, bolgenin bu kadar ATR disina konur

# Iki gurultu korumasi. Bunlar olmadan tarayici sahte firsat uretir:
# dar bir FVG, kucuk bir stop ve uzak bir hedefle "R:R 22" gosterir.
# Gercekte o stop ilk dakikada gurultuye takilir - R:R kagit uzerinde kalir.
ASGARI_BOLGE = 0.20      # bolge genisligi en az bu kadar 1H ATR olmali
ASGARI_STOP = 0.50       # stop mesafesi en az bu kadar 1H ATR olmali
AZAMI_RR = 12.0          # bunun ustu neredeyse her zaman olcum hatasidir


def _http(url, timeout=30):
    return json.load(urllib.request.urlopen(
        urllib.request.Request(url, headers=UA), timeout=timeout))


# ---------------------------------------------------------------- veri


def _yahoo(sembol, aralik, rng):
    # query1 zaman zaman 429/401 doner; query2 ayni veriyi verir. Tek
    # sunucuya bagli kalmak tarama turunu bosa dusuruyordu.
    son = None
    for sunucu in (1, 2):
        try:
            d = _http(f"https://query{sunucu}.finance.yahoo.com/v8/finance/"
                      f"chart/{sembol}?interval={aralik}&range={rng}")
            break
        except Exception as ex:
            son = ex
    else:
        raise son
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
    """N bari tek bara toplar (1H -> 4H icin kat=4).

    BIST seansi 24 saat degil; takvim kovasi yerine ardisik SEANS saatleri
    gruplanir - yoksa gun basi ve sonu yarim kovalar olusur.
    """
    out = []
    for i in range(0, len(barlar) - len(barlar) % kat, kat):
        g = barlar[i:i + kat]
        out.append({"t": g[0]["t"], "o": g[0]["o"],
                    "h": max(x["h"] for x in g), "l": min(x["l"] for x in g),
                    "c": g[-1]["c"], "v": sum(x["v"] for x in g)})
    return out


def veri_cek(ad, log=None):
    """Doner: {"1d":[...], "4h":[...], "1h":[...]}"""
    saatlik = _yahoo(f"{ad}.IS", "1h", "730d")
    return {"1d": _yahoo(f"{ad}.IS", "1d", "2y"),
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
    """Yonu GUNLUK yapi verir, 4H teyit eder.

    Doner: (yon, sebep, guc). guc: "uyumlu" | "notr" | "zayif" | "yok"
    Karasiz bir 4H, gunluge TERS bir 4H ile ayni sey degildir - 4H'nin
    susmasi bir itiraz degildir, o yuzden ayri derecelendirilir.
    """
    g = yapi_yonu(veri["1d"], n=2)
    d = yapi_yonu(veri["4h"], n=2)
    if g == "RANGE":
        return "RANGE", "Gunluk RANGE — yon yok, beklenir", "yok"
    if d == g:
        return g, f"Gunluk {g} + 4H {d} — uyumlu", "uyumlu"
    if d == "RANGE":
        return g, f"Gunluk {g} + 4H kararsiz — teyit yok", "notr"
    return g, f"Gunluk {g} + 4H {d} — 4H TERS, zayif", "zayif"


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
    yapisal bir bolge degil, tek bir mumun kuyrugudur.
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
            # Long icin bolge fiyatin ALTINDA olmali (dusup gelecek).
            # Zaten gecilmis bolge kurulum degildir.
            if tip == "bull" and g["ust"] > fiyat * 1.001:
                continue
            if tip == "bear" and g["alt"] < fiyat * 0.999:
                continue
            out.append({"periyot": etiket, "tip": tip,
                        "alt": g["alt"], "ust": g["ust"],
                        "uzaklik": abs(fiyat - (g["alt"] + g["ust"]) / 2)})
    out.sort(key=lambda z: z["uzaklik"])
    return out


# ------------------------------------------------------------ 2b) SUPURME


def _esit_uc_sayisi(bars, seviye, tolerans, dip_mi):
    """Supurulen seviyede kac swing ucu ust uste binmis.

    Tek bir dip supurulmesi bir seydir; UC dibin ayni yerde hizalanip
    hepsinin birden supurulmesi baska seydir - ikincisinde altta gercek bir
    stop havuzu vardi.
    """
    if tolerans <= 0:
        return 0
    ph, pl = pivotlar(bars, n=2)
    kaynak = pl if dip_mi else ph
    return sum(1 for _, s in kaynak if abs(s - seviye) <= tolerans)


def supurme_bul(bars, pencere=6, referans=30):
    """Likidite supurmesi: referans araligin ucu kirildi ama bar ICERI kapandi.

    Kirilan tarafta duran stoplar toplandi ve fiyat geri alindi. Bu bir
    "yanlis kirilim" degil - karsi tarafin yakitidir.

    Kapanis sarti kritik: kapanis da disarida kaldiysa o supurme degil,
    gercek kirilimdir.
    """
    if len(bars) < pencere + referans:
        return {"ssl": None, "bsl": None}
    ref = bars[-(pencere + referans):-pencere]
    son = bars[-pencere:]
    dip = min(b["l"] for b in ref)
    tepe = max(b["h"] for b in ref)
    a = atr(bars) or 0.0
    tol = a * 0.15
    ssl = bsl = None
    for j, b in enumerate(son):
        onceki = len(son) - 1 - j          # kac bar once oldu (0 = son bar)
        if b["l"] < dip and b["c"] > dip:
            ssl = {"seviye": dip, "t": b["t"], "sarkma": dip - b["l"],
                   "bar_once": onceki, "geri_alim": b["c"] - dip, "atr": a,
                   "esit_uc": _esit_uc_sayisi(ref, dip, tol, True)}
        if b["h"] > tepe and b["c"] < tepe:
            bsl = {"seviye": tepe, "t": b["t"], "sarkma": b["h"] - tepe,
                   "bar_once": onceki, "geri_alim": tepe - b["c"], "atr": a,
                   "esit_uc": _esit_uc_sayisi(ref, tepe, tol, False)}
    return {"ssl": ssl, "bsl": bsl}


def supurme_gucu(kayit):
    """Bir supurmenin kalitesi: 0.0 - 1.0.

    "Supurme var/yok" sorusu, bir ay onceki 1H'de yarim puanlik bir cizigi,
    dun 4H'de uc dibi birden alip sert donen hareketle ayni kefeye koyardi.
      periyot   4H yapisi 1H'den agir basar
      tazelik   iki bar oncesine kadar olan supurme hala "sicak"
      derinlik  ATR'nin dortte birinden derin sarkma gercek stop avidir
      havuz     ust uste binmis uclar = gercekten toplanacak stop vardi
    """
    g = 0.45
    if kayit.get("periyot") == "4H":
        g += 0.20
    if kayit.get("bar_once", 99) <= 2:
        g += 0.15
    a = kayit.get("atr") or 0.0
    if a and kayit.get("sarkma", 0) >= a * 0.25:
        g += 0.10
    if kayit.get("esit_uc", 0) >= 2:
        g += 0.10
    return min(g, 1.0)


def supurme_katmani(veri, yon):
    """4H ve 1H'de supurme arar, yonle uyumlu/uyumsuz diye ayirir."""
    lehte, aleyhte = [], []
    aranan = "ssl" if yon == "BULLISH" else "bsl"
    for etiket, bars, pencere in (("4H", veri["4h"], 4), ("1H", veri["1h"], 8)):
        s = supurme_bul(bars, pencere=pencere)
        for tip in ("ssl", "bsl"):
            kayit = s.get(tip)
            if not kayit:
                continue
            k = {"periyot": etiket, "tip": tip, **kayit}
            k["guc"] = supurme_gucu(k)
            (lehte if tip == aranan else aleyhte).append(k)
    lehte.sort(key=lambda z: z["guc"], reverse=True)
    return {"lehte": lehte, "aleyhte": aleyhte}


# ---------------------------------------------------------------- 3) VARIS


def kurulum_kur(ad, veri):
    """Yon + bolge + hedef birlestirilip somut bir islem plani cikarilir."""
    fiyat = veri["1h"][-1]["c"]
    a1 = atr(veri["1h"])
    yon, yon_sebep, yon_guc = yon_belirle(veri)
    sonuc = {"ad": ad, "fiyat": fiyat, "yon": yon, "yon_sebep": yon_sebep,
             "yon_guc": yon_guc, "atr_1h": a1, "durum": "YON YOK",
             "bolge": None, "plan": None, "supurme": None}
    if yon == "RANGE" or not a1:
        return sonuc

    # BIST long-only: asagi yonde kurulum aranmaz, bolge bile hesaplanmaz
    # ki rapor "alinamayacak islem" gostermesin.
    if yon == "BEARISH":
        sonuc["durum"] = "SHORT YON — BIST'te yalniz long alinir, atlandi"
        return sonuc

    sonuc["supurme"] = supurme_katmani(veri, yon)

    zonlar = bolgeler(veri, yon, a1)
    if not zonlar:
        sonuc["durum"] = "BOLGE YOK (yeterli genislikte)"
        return sonuc

    z = zonlar[0]
    sonuc["bolge"] = z
    ustler, _ = likidite_seviyeleri(veri["4h"])

    giris = min(z["ust"], fiyat)
    stop = z["alt"] - a1 * STOP_PAYI
    hedefler = [s for s in ustler if s > giris]
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
        stop = giris - risk

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
    sonuc["durum"] = "BOLGEDE — GIRIS SARTLARI TAMAM" if icinde else "BEKLEMEDE"
    return sonuc


# ---------------------------------------------------------------- rapor


def f(x, ond=2):
    return "-" if x is None else f"{x:,.{ond}f}"


def _saat(t):
    return f"{datetime.fromtimestamp(t, timezone.utc):%d.%m %H:%M}"


def yazdir(s):
    print(f"\n{'=' * 70}")
    print(f"{s['ad']}   fiyat {f(s['fiyat'])}   —   {s['durum']}")
    print(f"{'=' * 70}")
    print(f"1) YON    : {s['yon']}")
    print(f"           {s['yon_sebep']}")
    if s["durum"].startswith("SHORT YON"):
        print("           BIST long-only — asagi yonde kurulum aranmadi.")
        return
    z = s["bolge"]
    if z:
        print(f"2) BOLGE  : {z['periyot']} {z['tip']} FVG   "
              f"{f(z['alt'])} — {f(z['ust'])}")
    else:
        print("2) BOLGE  : yonle uyumlu, fiyatin onunde bolge yok")
    sp = s.get("supurme")
    if sp is not None:
        print("2b) SUPURME:")
        for k in sp["lehte"]:
            ne = "dip" if k["tip"] == "ssl" else "tepe"
            print(f"           ✅ {k['periyot']} {ne} supuruldu  {f(k['seviye'])}"
                  f"  guc %{k['guc'] * 100:.0f}")
            print(f"              {_saat(k['t'])} ({k['bar_once']} bar once)"
                  f"  sarkma {f(k['sarkma'])}  ust uste {k['esit_uc']} uc")
        for k in sp["aleyhte"]:
            ne = "dip" if k["tip"] == "ssl" else "tepe"
            print(f"           ⚠️ {k['periyot']} {ne} supuruldu  {f(k['seviye'])}"
                  f"  — yonun aleyhine")
        if not sp["lehte"] and not sp["aleyhte"]:
            print("           supurme yok — likidite hala duruyor")
    p = s["plan"]
    if p:
        print(f"3) PLAN   : giris {f(p['giris'])}   stop {f(p['stop'])}   "
              f"hedef {f(p['hedef'])}")
        print(f"           risk {f(p['risk'])}   odul {f(p['odul'])}   "
              f"R:R = {p['rr']:.1f}"
              + ("   (stop gurultu tabanina genisletildi)"
                 if p.get('stop_genisletildi') else ""))


def main():
    if len(sys.argv) > 1:
        hedefler = [sys.argv[1].upper()]
    else:
        from smc_watch import TAKIP_LISTESI
        hedefler = list(TAKIP_LISTESI)
    print(f"BIST SMC taramasi — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC")
    print(f"Periyotlar: Gunluk / 4H / 1H   ·   Kabul esigi: R:R >= {ASGARI_RR}")
    print("BIST yalniz LONG")
    for ad in hedefler:
        try:
            veri = veri_cek(ad)
        except Exception as ex:
            print(f"\n{ad}: veri alinamadi ({type(ex).__name__}: {ex})")
            continue
        yazdir(kurulum_kur(ad, veri))
    return 0


if __name__ == "__main__":
    sys.exit(main())
