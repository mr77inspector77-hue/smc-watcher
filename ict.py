# -*- coding: utf-8 -*-
"""ICT konsept motoru - Faz 1.

Bu moduldeki her fonksiyon DETERMINISTIK ve YANLISLANABILIRDIR.
Yorum yok, tahmin yok: girdi barlar, cikti tanimli bir etiket ve
etiketin hangi kanitlara dayandigi.

Icerik:
  gunluk_bias   -> Daily Bias
  haftalik_bias -> Weekly Bias
  po3           -> Power of Three (Birikim / Manipulasyon / Dagitim)
  smt           -> SMT Divergence (korele enstrumanla uyusmazlik)

Dealing Range ve likidite supurmesi (Turtle Soup) zaten smc_watch.py
icinde mevcuttur; burada tekrarlanmaz.
"""

from datetime import datetime, timezone

from seanslar import ts_to_et


# ---------------------------------------------------------------- yardimci


def _pivotlar(bars, n=2):
    yuksek, dusuk = [], []
    for i in range(n, len(bars) - n):
        pencere = bars[i - n: i + n + 1]
        if bars[i]["h"] == max(b["h"] for b in pencere):
            yuksek.append((i, bars[i]["h"]))
        if bars[i]["l"] == min(b["l"] for b in pencere):
            dusuk.append((i, bars[i]["l"]))
    return yuksek, dusuk


def _yapi_oyu(bars, n=2):
    """HH+HL -> +1, LH+LL -> -1, aksi 0."""
    ph, pl = _pivotlar(bars, n=n)
    if len(ph) < 2 or len(pl) < 2:
        return 0, "yeterli pivot yok"
    hh, hl = ph[-1][1] > ph[-2][1], pl[-1][1] > pl[-2][1]
    lh, ll = ph[-1][1] < ph[-2][1], pl[-1][1] < pl[-2][1]
    if hh and hl:
        return 1, "yukselen tepe + yukselen dip"
    if lh and ll:
        return -1, "alcalan tepe + alcalan dip"
    return 0, "yapi karisik"


def _kapanis_oyu(bar):
    """Mumun kapanisi kendi araliginin neresinde? ust 1/3 -> +1, alt 1/3 -> -1."""
    aralik = bar["h"] - bar["l"]
    if aralik <= 0:
        return 0, "aralik sifir"
    konum = (bar["c"] - bar["l"]) / aralik
    if konum >= 0.66:
        return 1, f"kapanis aralikta %{konum * 100:.0f} (ust bolge)"
    if konum <= 0.34:
        return -1, f"kapanis aralikta %{konum * 100:.0f} (alt bolge)"
    return 0, f"kapanis aralikta %{konum * 100:.0f} (orta)"


def _etiket(toplam, esik=2):
    if toplam >= esik:
        return "BULLISH"
    if toplam <= -esik:
        return "BEARISH"
    return "RANGE"


# ---------------------------------------------------------------- Daily Bias


def gunluk_bias(gunluk, fiyat, bugun_tepe=None, bugun_dip=None):
    """Daily Bias - 3 oylu, aciklanabilir.

    1) Gunluk yapi (HH/HL - LH/LL)
    2) Onceki gunun kapanis konumu
    3) Cekim (draw on liquidity): hangi taraftaki likidite HENUZ ALINMADI

    3. oy icin bugunun tepe/dibi gerekir: PDH bugun zaten alindiysa oradaki
    likidite tuketilmistir, hedef artik asagidaki PDL'dir. Bu kontrol
    olmadan oy sadece "fiyat ortanin ustunde mi" demeye indirgenir ve
    bilgi tasimaz.

    Donen: {"bias","toplam","oylar",...,"pdh_alindi","pdl_alindi"}
    """
    if not gunluk or len(gunluk) < 6:
        return {"bias": "RANGE", "toplam": 0, "oylar": ["gunluk veri yetersiz"],
                "pdh": None, "pdl": None, "pdh_alindi": False,
                "pdl_alindi": False}

    onceki = gunluk[-2] if len(gunluk) >= 2 else gunluk[-1]
    pdh, pdl = onceki["h"], onceki["l"]
    oylar, toplam = [], 0

    o, sebep = _yapi_oyu(gunluk, n=2)
    toplam += o
    oylar.append(f"Yapi: {sebep} ({o:+d})")

    o, sebep = _kapanis_oyu(onceki)
    toplam += o
    oylar.append(f"Onceki gun: {sebep} ({o:+d})")

    pdh_alindi = bugun_tepe is not None and bugun_tepe > pdh
    pdl_alindi = bugun_dip is not None and bugun_dip < pdl

    if pdh_alindi and not pdl_alindi:
        toplam -= 1
        oylar.append(f"Cekim: PDH {pdh:.2f} ALINDI, kalan hedef PDL "
                     f"{pdl:.2f} (-1)")
    elif pdl_alindi and not pdh_alindi:
        toplam += 1
        oylar.append(f"Cekim: PDL {pdl:.2f} ALINDI, kalan hedef PDH "
                     f"{pdh:.2f} (+1)")
    elif pdh_alindi and pdl_alindi:
        oylar.append("Cekim: her iki taraf da alindi, yon bilgisi yok (+0)")
    else:
        # Ikisi de duruyor -> fiyat hangisine uzaksa oraya cekilir
        orta = (pdh + pdl) / 2.0
        if fiyat < orta:
            toplam += 1
            oylar.append(f"Cekim: iki taraf da duruyor, PDH {pdh:.2f} "
                         f"daha uzak hedef (+1)")
        else:
            toplam -= 1
            oylar.append(f"Cekim: iki taraf da duruyor, PDL {pdl:.2f} "
                         f"daha uzak hedef (-1)")

    return {"bias": _etiket(toplam, 2), "toplam": toplam, "oylar": oylar,
            "pdh": pdh, "pdl": pdl,
            "pdh_alindi": pdh_alindi, "pdl_alindi": pdl_alindi}


# ---------------------------------------------------------------- Weekly Bias


def gunluk_to_haftalik(gunluk):
    """Gunluk barlari ISO haftasina toplar."""
    out = {}
    for b in gunluk:
        d = datetime.fromtimestamp(b["t"], timezone.utc).isocalendar()
        k = (d[0], d[1])
        if k not in out:
            out[k] = {"t": b["t"], "o": b["o"], "h": b["h"], "l": b["l"],
                      "c": b["c"], "v": b.get("v", 0)}
        else:
            g = out[k]
            g["h"] = max(g["h"], b["h"])
            g["l"] = min(g["l"], b["l"])
            g["c"] = b["c"]
            g["v"] += b.get("v", 0)
    return [out[k] for k in sorted(out)]


def haftalik_bias(gunluk):
    """Weekly Bias - 2 oy: haftalik yapi + onceki haftanin kapanis konumu."""
    haftalik = gunluk_to_haftalik(gunluk or [])
    if len(haftalik) < 5:
        return {"bias": "RANGE", "toplam": 0, "oylar": ["haftalik veri yetersiz"],
                "pwh": None, "pwl": None}

    onceki = haftalik[-2]
    oylar, toplam = [], 0

    o, sebep = _yapi_oyu(haftalik, n=1)
    toplam += o
    oylar.append(f"Haftalik yapi: {sebep} ({o:+d})")

    o, sebep = _kapanis_oyu(onceki)
    toplam += o
    oylar.append(f"Onceki hafta: {sebep} ({o:+d})")

    return {"bias": _etiket(toplam, 1), "toplam": toplam, "oylar": oylar,
            "pwh": onceki["h"], "pwl": onceki["l"]}


# ---------------------------------------------------------------- Power of Three


FAZ_ETIKET = {
    "BIRIKIM": "Birikim (accumulation)",
    "MANIPULASYON": "Manipülasyon (Judas)",
    "DAGITIM": "Dağıtım (distribution)",
    "BELIRSIZ": "Faz okunamadı",
}


def po3(bars, birikim_dk=120, bist=False):
    """Power of Three: gunun mumunu Birikim -> Manipulasyon -> Dagitim
    olarak okur.

    Birikim penceresi:
      - 24 saat islenen enstrumanlar: gunun ilk `birikim_dk` dakikasi (ET 00:00'dan)
      - BIST: seans acilisindan itibaren ilk `birikim_dk` dakika

    Manipulasyon = birikim araliginin bir ucunun supurulup ICERI kapanmasi.
    Dagitim      = manipulasyonun TERS ucundan kapanisla cikilmasi.
    """
    bos = {"faz": "BELIRSIZ", "yon": None, "birikim": None, "manip_seviye": None,
           "aciklama": "gün içi bar yok"}
    if not bars:
        return bos

    son_et = ts_to_et(bars[-1]["t"])
    bugun = [b for b in bars if ts_to_et(b["t"]).date() == son_et.date()]
    if len(bugun) < 4:
        return dict(bos, aciklama="gün içi bar sayısı yetersiz")

    bas_ts = bugun[0]["t"]
    birikim = [b for b in bugun if (b["t"] - bas_ts) < birikim_dk * 60]
    sonrasi = [b for b in bugun if (b["t"] - bas_ts) >= birikim_dk * 60]
    if len(birikim) < 2:
        return dict(bos, aciklama="birikim penceresi henüz oluşmadı")

    b_hi = max(b["h"] for b in birikim)
    b_lo = min(b["l"] for b in birikim)
    kutu = {"tepe": b_hi, "dip": b_lo, "bas_ts": birikim[0]["t"],
            "bit_ts": birikim[-1]["t"]}

    if not sonrasi:
        return {"faz": "BIRIKIM", "yon": None, "birikim": kutu,
                "manip_seviye": None,
                "aciklama": f"Birikim sürüyor: {b_lo:.2f} - {b_hi:.2f}"}

    # Manipulasyon ara: birikim ucunu delip iceri kapanan ilk bar
    manip_yon = manip_sev = manip_i = None
    for i, b in enumerate(sonrasi):
        if b["l"] < b_lo and b["c"] > b_lo:
            manip_yon, manip_sev, manip_i = "bull", b_lo, i
            break
        if b["h"] > b_hi and b["c"] < b_hi:
            manip_yon, manip_sev, manip_i = "bear", b_hi, i
            break

    if manip_yon is None:
        son = sonrasi[-1]["c"]
        if son > b_hi:
            return {"faz": "DAGITIM", "yon": "bull", "birikim": kutu,
                    "manip_seviye": None,
                    "aciklama": "Manipülasyon olmadan yukarı kırılım — "
                                "PO3 şablonuna uymuyor, dikkat"}
        if son < b_lo:
            return {"faz": "DAGITIM", "yon": "bear", "birikim": kutu,
                    "manip_seviye": None,
                    "aciklama": "Manipülasyon olmadan aşağı kırılım — "
                                "PO3 şablonuna uymuyor, dikkat"}
        return {"faz": "BIRIKIM", "yon": None, "birikim": kutu,
                "manip_seviye": None,
                "aciklama": f"Fiyat hâlâ birikim içinde: {b_lo:.2f} - {b_hi:.2f}"}

    # Dagitim: manipulasyon sonrasi ters uctan kapanisla cikildi mi
    kalan = sonrasi[manip_i + 1:]
    if kalan:
        if manip_yon == "bull" and any(b["c"] > b_hi for b in kalan):
            return {"faz": "DAGITIM", "yon": "bull", "birikim": kutu,
                    "manip_seviye": manip_sev,
                    "aciklama": f"{manip_sev:.2f} süpürüldü, {b_hi:.2f} "
                                f"üzerinde kapanış — yukarı dağıtım"}
        if manip_yon == "bear" and any(b["c"] < b_lo for b in kalan):
            return {"faz": "DAGITIM", "yon": "bear", "birikim": kutu,
                    "manip_seviye": manip_sev,
                    "aciklama": f"{manip_sev:.2f} süpürüldü, {b_lo:.2f} "
                                f"altında kapanış — aşağı dağıtım"}

    yon_metin = "aşağı süpürme → yukarı bekleniyor" if manip_yon == "bull" \
        else "yukarı süpürme → aşağı bekleniyor"
    return {"faz": "MANIPULASYON", "yon": manip_yon, "birikim": kutu,
            "manip_seviye": manip_sev,
            "aciklama": f"{manip_sev:.2f} süpürüldü ({yon_metin}); "
                        f"dağıtım teyidi henüz yok"}


# ---------------------------------------------------------------- SMT


def _es_bar(harita, ts, tolerans=1):
    """Ikinci enstrumanda ayni zaman damgasindaki bar (komsuluk toleransli)."""
    aday = [harita[ts + k * 900] for k in range(-tolerans, tolerans + 1)
            if (ts + k * 900) in harita]
    return aday or None


def smt(ana, ikinci, ikinci_ad="korele", n=2, pencere=80):
    """SMT Divergence: iki korele enstrumanin AYNI ANDA ayni yapiyi
    yapmamasi.

    Ana enstrumanda daha yuksek tepe olusurken korele enstrumanda
    olusmuyorsa -> yukari hareket teyitsiz (bearish SMT).
    Ana enstrumanda daha dusuk dip olusurken korele enstrumanda
    olusmuyorsa -> asagi hareket teyitsiz (bullish SMT).

    Donen: {"var","tip","aciklama","ad"}  tip: "bull" | "bear" | None
    """
    yok = {"var": False, "tip": None, "ad": ikinci_ad, "aciklama": None}
    if not ana or not ikinci or len(ana) < 20 or len(ikinci) < 20:
        return dict(yok, aciklama="korele veri yok")

    a = ana[-pencere:]
    harita = {b["t"]: b for b in ikinci}
    ph, pl = _pivotlar(a, n=n)

    if len(ph) >= 2:
        t1, t2 = a[ph[-2][0]]["t"], a[ph[-1][0]]["t"]
        k1, k2 = _es_bar(harita, t1), _es_bar(harita, t2)
        if k1 and k2:
            i1, i2 = max(x["h"] for x in k1), max(x["h"] for x in k2)
            if ph[-1][1] > ph[-2][1] and i2 < i1:
                return {"var": True, "tip": "bear", "ad": ikinci_ad,
                        "aciklama": f"Ana enstrüman yeni tepe yaptı, {ikinci_ad} "
                                    f"yapamadı ({i1:.2f} → {i2:.2f}). "
                                    f"Yukarı hareket teyitsiz."}

    if len(pl) >= 2:
        t1, t2 = a[pl[-2][0]]["t"], a[pl[-1][0]]["t"]
        k1, k2 = _es_bar(harita, t1), _es_bar(harita, t2)
        if k1 and k2:
            i1, i2 = min(x["l"] for x in k1), min(x["l"] for x in k2)
            if pl[-1][1] < pl[-2][1] and i2 > i1:
                return {"var": True, "tip": "bull", "ad": ikinci_ad,
                        "aciklama": f"Ana enstrüman yeni dip yaptı, {ikinci_ad} "
                                    f"yapmadı ({i1:.2f} → {i2:.2f}). "
                                    f"Aşağı hareket teyitsiz."}

    return dict(yok, aciklama=f"{ikinci_ad} ile uyuşmazlık yok")
