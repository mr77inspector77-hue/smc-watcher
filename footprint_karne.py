# -*- coding: utf-8 -*-
"""Footprint karnesi: order flow metrikleri bilgi tasiyor mu?

Sinyal motoruna BAGLAMADAN once olcer. Yontem geri_test.py ile ayni tutuldu
ki sayilar dogrudan karsilastirilabilsin:
    1 ATR stop / 2 ATR hedef, 32 bar zaman siniri, ayni barda ikisi de
    dokunulursa KAYIP (kotumser).
2R'de basabas isabet %33.3'tur - karsilastirma cizgisi budur.

Ham islem verisi indirilip 15dk footprint barlarina donusturulur; barlarin
OZETI onbellege yazilir (ham veri ~8 MB/gun, ozet ~50 KB/gun).

Kullanim:
    python footprint_karne.py            # 20 gun
    python footprint_karne.py 45         # 45 gun
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import footprint as FP

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SEMBOL = "BTCUSDT"
ONBELLEK = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "footprint_onbellek")
ILERI = 32
HEDEF_R = 2.0
ATR_N = 14


# ---------------------------------------------------------------- veri


def gun_ozetleri(tarih):
    """Bir gunun 15dk footprint bar ozetleri. Onbellekten okur veya uretir."""
    os.makedirs(ONBELLEK, exist_ok=True)
    yol = os.path.join(ONBELLEK, f"{SEMBOL}-{tarih}.json")
    if os.path.exists(yol):
        with open(yol, encoding="utf-8") as f:
            return json.load(f)

    islemler = FP.arsiv_gun(SEMBOL, tarih)
    if not islemler:
        return []
    barlar = FP.footprint_barlar(islemler, 900)
    ozetler = [FP.ozet(b) for b in barlar]
    with open(yol, "w", encoding="utf-8") as f:
        json.dump(ozetler, f)
    print(f"  {tarih}: {len(islemler):>9,} islem -> {len(ozetler)} bar")
    return ozetler


def veri_topla(gun):
    bugun = datetime.now(timezone.utc).date()
    hepsi = []
    for i in range(gun, 0, -1):
        t = (bugun - timedelta(days=i)).strftime("%Y-%m-%d")
        hepsi.extend(gun_ozetleri(t))
    hepsi.sort(key=lambda b: b["t"])
    return hepsi


# ---------------------------------------------------------------- olcum


def atr(barlar, i, n=ATR_N):
    if i < n:
        return None
    toplam = 0.0
    for j in range(i - n + 1, i + 1):
        onceki = barlar[j - 1]["c"]
        toplam += max(barlar[j]["h"] - barlar[j]["l"],
                      abs(barlar[j]["h"] - onceki),
                      abs(barlar[j]["l"] - onceki))
    return toplam / n


def sonuc_olc(barlar, i, yon, birim):
    giris = barlar[i]["c"]
    if yon == "long":
        stop, hedef = giris - birim, giris + birim * HEDEF_R
    else:
        stop, hedef = giris + birim, giris - birim * HEDEF_R
    for b in barlar[i + 1: i + 1 + ILERI]:
        if yon == "long":
            s, h = b["l"] <= stop, b["h"] >= hedef
        else:
            s, h = b["h"] >= stop, b["l"] <= hedef
        if s:
            return "KAYIP"
        if h:
            return "KAZANC"
    return "ZAMAN"


def kosullari_uret(barlar):
    """Her bar icin footprint kosullarini ve sonuclarini uretir."""
    cvd, toplam = [], 0.0
    for b in barlar:
        toplam += b["delta"]
        cvd.append(toplam)

    kayitlar = []
    for i in range(ATR_N + 5, len(barlar) - 1):
        b = barlar[i]
        birim = atr(barlar, i)
        if not birim or b["hacim"] <= 0:
            continue
        do = b["delta_orani"]
        dz_fark = b["dengesizlik_alis"] - b["dengesizlik_satis"]

        # CVD uyusmazligi: fiyat yeni dip, CVD yeni dip yapmiyor (veya tersi)
        gec = 8
        cvd_bull = (b["l"] < min(x["l"] for x in barlar[i - gec:i])
                    and cvd[i] > min(cvd[i - gec:i]))
        cvd_bear = (b["h"] > max(x["h"] for x in barlar[i - gec:i])
                    and cvd[i] < max(cvd[i - gec:i]))

        kayitlar.append({
            "i": i,
            "delta_orani": do,
            "dz_fark": dz_fark,
            "emilim": b["emilim"],
            "cvd_bull": cvd_bull,
            "cvd_bear": cvd_bear,
            "birim": birim,
            "long": sonuc_olc(barlar, i, "long", birim),
            "short": sonuc_olc(barlar, i, "short", birim),
        })
    return kayitlar


# ---------------------------------------------------------------- rapor


def _oran(kayitlar, yon):
    karar = [k for k in kayitlar if k[yon] != "ZAMAN"]
    if not karar:
        return None, 0
    return 100.0 * sum(1 for k in karar if k[yon] == "KAZANC") / len(karar), len(karar)


def _satir(etiket, kayitlar, yon, taban):
    oran, n = _oran(kayitlar, yon)
    if oran is None:
        return f"  {etiket:<34} {'-':>7}      0 karar"
    fark = f"  ({oran - taban:+.1f})" if taban is not None else ""
    uyari = "" if n >= 30 else "   ⚠ kucuk"
    return f"  {etiket:<34} {oran:>6.1f}%  {n:>5} karar{fark}{uyari}"


def rapor(kayitlar):
    tl, nl = _oran(kayitlar, "long")
    ts, ns = _oran(kayitlar, "short")
    print(f"\n{'=' * 72}")
    print(f"TABAN — kosulsuz her bar")
    print(f"  long  {tl:.1f}%  ({nl} karar)      short {ts:.1f}%  ({ns} karar)")
    print(f"  2R basabas cizgisi: %33.3")
    print(f"{'=' * 72}")

    print("\n1) DELTA ORANI  — agresif taraf baskinsa yon devam eder mi?")
    for etiket, sec, yon, taban in [
        ("delta > +%40 → long", lambda k: k["delta_orani"] > 0.40, "long", tl),
        ("delta > +%40 → short (ters)", lambda k: k["delta_orani"] > 0.40, "short", ts),
        ("delta < -%40 → short", lambda k: k["delta_orani"] < -0.40, "short", ts),
        ("delta < -%40 → long (ters)", lambda k: k["delta_orani"] < -0.40, "long", tl),
    ]:
        print(_satir(etiket, [k for k in kayitlar if sec(k)], yon, taban))

    print("\n2) EMILIM  — guclu delta, zayif fiyat: donus adayi")
    print(_satir("alis emildi → short", [k for k in kayitlar if k["emilim"] == "alis_emildi"], "short", ts))
    print(_satir("alis emildi → long (ters)", [k for k in kayitlar if k["emilim"] == "alis_emildi"], "long", tl))
    print(_satir("satis emildi → long", [k for k in kayitlar if k["emilim"] == "satis_emildi"], "long", tl))
    print(_satir("satis emildi → short (ters)", [k for k in kayitlar if k["emilim"] == "satis_emildi"], "short", ts))

    print("\n3) DIAGONAL DENGESIZLIK")
    print(_satir("alis dengesizligi baskin → long", [k for k in kayitlar if k["dz_fark"] >= 4], "long", tl))
    print(_satir("satis dengesizligi baskin → short", [k for k in kayitlar if k["dz_fark"] <= -4], "short", ts))

    print("\n4) CVD UYUSMAZLIGI  — fiyat yeni ucta, kumulatif delta teyit etmiyor")
    print(_satir("bullish uyusmazlik → long", [k for k in kayitlar if k["cvd_bull"]], "long", tl))
    print(_satir("bearish uyusmazlik → short", [k for k in kayitlar if k["cvd_bear"]], "short", ts))


def main():
    gun = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    print(f"Footprint karnesi — {SEMBOL}, son {gun} gun, 15dk bar")
    print(f"1 ATR stop / {HEDEF_R} ATR hedef / {ILERI} bar sinir\n")
    barlar = veri_topla(gun)
    if len(barlar) < 200:
        print(f"Yetersiz veri ({len(barlar)} bar)")
        return 1
    ilk = datetime.fromtimestamp(barlar[0]["t"], timezone.utc)
    son = datetime.fromtimestamp(barlar[-1]["t"], timezone.utc)
    print(f"\n{len(barlar)} bar  ({ilk:%Y-%m-%d} → {son:%Y-%m-%d})")
    rapor(kosullari_uret(barlar))
    print("\n" + "=" * 72)
    print("Bir kosul tabanin BELIRGIN ustunde degilse o metrik bu enstrumanda,\n"
          "bu periyotta, bu olcuyle bilgi tasimiyor demektir. 'Ters' satirlar\n"
          "kontrol amaclidir: ikisi de tabana yakinsa metrik yonsuzdur.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
