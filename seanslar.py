# -*- coding: utf-8 -*-
"""Zaman katmani: seanslar ve kill zone'lar.

ICT konseptlerinin TAMAMI saate baglidir. Bu modul tek is yapar:
bir UTC zaman damgasini New York saatine cevirir ve o anin hangi
seans / kill zone icinde oldugunu soyler.

DST (ABD yaz saati) EL ILE hesaplanir; zoneinfo/tzdata kurulu olmayan
Windows makinelerinde de calissin diye. Kural (2007 sonrasi ABD):
  yaz saati = Mart'in 2. Pazari 02:00 yerel  ->  Kasim'in 1. Pazari 02:00 yerel

Turkiye (TSI) 2016'dan beri kalici UTC+3, DST yok.
Bu yuzden ET <-> TSI farki yazin 7, kisin 8 saattir. Sabit varsaymak
kasim-mart arasi TUM kill zone hesaplarini bir saat kaydirir.
"""

from datetime import datetime, timedelta, timezone

TSI = timezone(timedelta(hours=3))

# ---------------------------------------------------------------- DST


def _ayin_n_inci_pazari(yil, ay, n):
    """Ayin n. Pazar gununun gun numarasi (n=1 ilk Pazar)."""
    d = datetime(yil, ay, 1)
    # weekday(): Pazartesi=0 ... Pazar=6
    ilk = 1 + (6 - d.weekday()) % 7
    return ilk + (n - 1) * 7


def et_ofset_saat(u):
    """Verilen UTC datetime icin ET ofseti: -4 (EDT) veya -5 (EST)."""
    y = u.year
    # Gecis anlari UTC olarak: 2. Pazar Mart 07:00 UTC, 1. Pazar Kasim 06:00 UTC
    basla = datetime(y, 3, _ayin_n_inci_pazari(y, 3, 2), 7, tzinfo=timezone.utc)
    bitir = datetime(y, 11, _ayin_n_inci_pazari(y, 11, 1), 6, tzinfo=timezone.utc)
    return -4 if basla <= u < bitir else -5


def utc_to_et(u):
    """UTC datetime -> ET datetime (naive degil, sabit ofsetli)."""
    ofs = et_ofset_saat(u)
    return u.astimezone(timezone(timedelta(hours=ofs)))


def ts_to_et(ts):
    return utc_to_et(datetime.fromtimestamp(ts, timezone.utc))


def ts_to_tsi(ts):
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(TSI)


# ---------------------------------------------------------------- kill zone'lar

# (kod, etiket, baslangic_dk, bitis_dk, tetik_mi)   -- ET dakikasi cinsinden
KILL_ZONE = [
    ("ASYA",    "Asya seansı",            20 * 60, 24 * 60, False),
    ("LONDRA",  "Londra kill zone",        2 * 60,  5 * 60, True),
    ("NY_AM",   "New York AM kill zone",   7 * 60, 10 * 60, True),
    ("SB_AM",   "Silver Bullet (AM)",     10 * 60, 11 * 60, True),
    ("OGLE",    "NY öğle ölü bölgesi",    12 * 60, 13 * 60, False),
    ("SB_PM",   "Silver Bullet (PM)",     14 * 60, 15 * 60, True),
]

# BIST'in ICT karsiligi YOKTUR. Asya/Londra seansi BIST icin anlamsizdir.
# Bunlar BIST'in kendi hacim yapisindan cikarilmis pencerelerdir (TSI).
BIST_PENCERE = [
    ("ACILIS",     "Açılış oynaklığı",      10 * 60,      10 * 60 + 30, True),
    ("SABAH",      "Sabah seansı",          10 * 60 + 30, 12 * 60 + 30, False),
    ("OLU",        "Öğle ölü bölgesi",      12 * 60 + 30, 13 * 60 + 30, False),
    ("OGLEDENSNR", "Öğleden sonra",         13 * 60 + 30, 16 * 60 + 30, False),
    ("NY_ORTUSME", "NY açılışı örtüşmesi",  16 * 60 + 30, 17 * 60 + 40, True),
    ("KAPANIS",    "Kapanış",               17 * 60 + 40, 18 * 60 + 15, True),
]


def _pencere_bul(dk, tablo):
    for kod, etiket, bas, bit, tetik in tablo:
        if bas <= dk < bit:
            return {"kod": kod, "etiket": etiket, "tetik": tetik}
    return None


def zaman_bilgisi(ts, bist=False):
    """Bir bar zaman damgasi icin seans baglami dondurur.

    Donen alanlar:
      et / tsi      : okunabilir saat metinleri
      kz            : {kod, etiket, tetik} veya None
      tetik         : bu an "tetik penceresi" mi (True) yoksa sadece izleme mi
      kapi          : "TETIK" | "IZLE"
    """
    et = ts_to_et(ts)
    tsi = ts_to_tsi(ts)
    if bist:
        p = _pencere_bul(tsi.hour * 60 + tsi.minute, BIST_PENCERE)
    else:
        p = _pencere_bul(et.hour * 60 + et.minute, KILL_ZONE)
    tetik = bool(p and p["tetik"])
    return {
        "et": et.strftime("%H:%M ET"),
        "tsi": tsi.strftime("%H:%M TSI"),
        "et_ofset": et_ofset_saat(datetime.fromtimestamp(ts, timezone.utc)),
        "kz": p,
        "tetik": tetik,
        "kapi": "TETIK" if tetik else "IZLE",
    }


def bar_kill_zone(ts, bist=False):
    """Grafik boyamasi icin: bir barin pencere kodu (veya None)."""
    z = zaman_bilgisi(ts, bist=bist)
    return z["kz"]["kod"] if z["kz"] else None


# ---------------------------------------------------------------- Asya araligi


def asya_araligi(bars):
    """En son TAMAMLANMIS Asya seansinin (20:00-00:00 ET) tepe/dibi.

    Judas Swing ve Londra manipulasyonu bu araligi referans alir.
    BIST icin cagrilmaz - karsiligi yoktur.
    Donen: {"tepe":..,"dip":..,"tarih":..,"bas_ts":..,"bit_ts":..} veya None
    """
    if not bars:
        return None
    gruplar = {}
    for b in bars:
        et = ts_to_et(b["t"])
        if 20 <= et.hour < 24:
            gruplar.setdefault(et.strftime("%Y-%m-%d"), []).append(b)
    if not gruplar:
        return None
    son_bar_et = ts_to_et(bars[-1]["t"])
    for tarih in sorted(gruplar, reverse=True):
        g = gruplar[tarih]
        # Seans TAMAMLANMIS olmali. Devam eden Asya seansinin tepe/dibi
        # referans olarak kullanilamaz - daha yukari/asagi gidebilir.
        if (son_bar_et - ts_to_et(g[-1]["t"])) < timedelta(minutes=30):
            continue
        return {
            "tepe": max(x["h"] for x in g),
            "dip": min(x["l"] for x in g),
            "tarih": tarih,
            "bas_ts": g[0]["t"],
            "bit_ts": g[-1]["t"],
            "bar_sayisi": len(g),
        }
    return None


# ---------------------------------------------------------------- seans acikligi


def nq_acik_mi(u=None):
    """CME e-mini seansi. DST'yi dogru hesaplar.
    Pazar 18:00 ET -> Cuma 17:00 ET, gunluk 17:00-18:00 ET bakim molasi."""
    u = u or datetime.now(timezone.utc)
    et = utc_to_et(u)
    gun, saat = et.weekday(), et.hour      # Pazartesi=0 ... Pazar=6
    if gun == 5:                            # Cumartesi
        return False
    if gun == 6 and saat < 18:              # Pazar 18:00 oncesi
        return False
    if gun == 4 and saat >= 17:             # Cuma 17:00 sonrasi
        return False
    return saat != 17                       # gunluk bakim


def bist_acik_mi(u=None):
    """BIST seansi: hafta ici 10:00 - 18:30 TSI. Yerel saate bagimli degil."""
    t = (u or datetime.now(timezone.utc)).astimezone(TSI)
    if t.weekday() > 4:
        return False
    dk = t.hour * 60 + t.minute
    return 10 * 60 <= dk <= 18 * 60 + 30
