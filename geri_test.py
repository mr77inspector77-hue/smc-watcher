# -*- coding: utf-8 -*-
"""Geri test: kill zone filtresi ve SMT vetosu gercekten ise yariyor mu?

Motoru gecmis barlar uzerinde bar bar yeniden oynatir, canli sistemin
urettigi sinyallerin AYNISINI uretir ve her sinyalin sonucunu mekanik
bir kuralla olcer.

DURUSTLUK KURALLARI (bunlar olmadan geri test kendini kandirir):
  1. Ileriye bakma yok. i. barda sadece bars[:i+1] gorulur.
  2. Gunluk barlar da 15dk gecmisinden turetilir - disaridan tam gunluk
     seri cekip "o gunun tamamini" bilmek ileriye bakmaktir.
  3. Sinyal sayimi canliyla ayni: sadece DURUM DEGISTIGINDE bir sinyal
     sayilir, her barda degil. Aksi halde ayni kurulum 20 kez sayilir.
  4. Sonuc olcusu sabit ve pesin tanimli: 1 ATR stop, 2 ATR hedef, 32 bar
     zaman siniri. Sonradan "su da olsaydi" ayari yapilmaz.

Kullanim:
    python geri_test.py                 # tum enstrumanlar, 60 gun
    python geri_test.py BTCUSDT 120     # tek enstruman, 120 gun
"""

import sys
from collections import defaultdict
from datetime import datetime, timezone

# Windows konsolu cp1254; ok/uyari isaretleri patlamasin
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import ict
import seanslar
import smc_watch as S
from veri_kaynaklari import korele_tarihsel, tarihsel_15m

ISINMA = 200          # motorun guvenilir calismasi icin gereken en az bar
PENCERE = 400         # her adimda motora verilen bar penceresi
ILERI = 32            # sonucu olcmek icin ileri bakilan bar sayisi (8 saat)
ATR_N = 14
HEDEF_R = 2.0         # 1 ATR risk, 2 ATR hedef


def atr(bars, n=ATR_N):
    if len(bars) < n + 1:
        return None
    toplam = 0.0
    for i in range(len(bars) - n, len(bars)):
        onceki = bars[i - 1]["c"]
        tr = max(bars[i]["h"] - bars[i]["l"],
                 abs(bars[i]["h"] - onceki),
                 abs(bars[i]["l"] - onceki))
        toplam += tr
    return toplam / n


def sonuc_olc(bars, i, yon, birim):
    """Sinyalden sonra hedef mi stop mu once geldi?

    Doner: "KAZANC" | "KAYIP" | "ZAMAN" (ikisi de gelmedi)
    Ayni bar icinde ikisi de dokunulduysa KAYIP sayilir - iyimser
    varsayim yapmak geri testin en yaygin yalanidir.
    """
    giris = bars[i]["c"]
    if yon == "long":
        stop, hedef = giris - birim, giris + birim * HEDEF_R
    else:
        stop, hedef = giris + birim, giris - birim * HEDEF_R

    for b in bars[i + 1: i + 1 + ILERI]:
        if yon == "long":
            stop_deydi, hedef_deydi = b["l"] <= stop, b["h"] >= hedef
        else:
            stop_deydi, hedef_deydi = b["h"] >= stop, b["l"] <= hedef
        if stop_deydi:
            return "KAYIP"          # ayni barda ikisi de -> kotumser
        if hedef_deydi:
            return "KAZANC"
    return "ZAMAN"


def gunluk_turet(bars, bist=False):
    """15dk barlardan gunluk bar uretir. ET gunu esas alinir (ICT'nin
    'midnight open' tanimi); BIST icin TSI takvim gunu."""
    gruplar = {}
    for b in bars:
        t = seanslar.ts_to_tsi(b["t"]) if bist else seanslar.ts_to_et(b["t"])
        k = t.strftime("%Y-%m-%d")
        g = gruplar.get(k)
        if g is None:
            gruplar[k] = {"t": b["t"], "o": b["o"], "h": b["h"], "l": b["l"],
                          "c": b["c"], "v": b.get("v", 0)}
        else:
            g["h"] = max(g["h"], b["h"])
            g["l"] = min(g["l"], b["l"])
            g["c"] = b["c"]
            g["v"] += b.get("v", 0)
    return [gruplar[k] for k in sorted(gruplar)]


def oynat(ad, bars, korele, korele_ad):
    """Motoru bar bar yeniden oynatir, sinyal listesi dondurur."""
    bist = ad in S.BIST_HISSELERI
    sinyaller = []
    onceki_durum = None

    for i in range(ISINMA, len(bars) - 1):
        gorulen = bars[max(0, i - PENCERE): i + 1]
        # korele seriyi de ayni ana kadar kirp - yoksa SMT gelecegi gorur
        kor = [b for b in korele if b["t"] <= bars[i]["t"]][-PENCERE:] \
            if korele else None
        gunluk = gunluk_turet(bars[:i + 1], bist)[-120:]

        r = S.degerlendir(ad, gorulen, S.resample_to_1h(gorulen),
                          gunluk, kor, korele_ad)

        if r["durum"] != onceki_durum:
            onceki_durum = r["durum"]
            if r["durum"] == "NOTR":
                continue
            birim = atr(gorulen)
            if not birim:
                continue
            # BIST'te acigga satis yok: short sinyali islem degildir
            if bist and r["yon"] == "short":
                continue
            sinyaller.append({
                "i": i,
                "zaman": datetime.fromtimestamp(bars[i]["t"], timezone.utc),
                "durum": r["durum"],
                "yon": r["yon"],
                "skor": r["skor"],
                "kapi": r["kapi"],
                "pencere": (r["zaman"]["kz"] or {}).get("kod", "YOK"),
                "smt": r["smt"]["tip"] if r["smt"]["var"] else None,
                "veto": bool(r["veto"]),
                "gunluk_bias": r["gunluk_bias"]["bias"],
                "po3": r["po3"]["faz"],
                "sonuc": sonuc_olc(bars, i, r["yon"], birim),
            })
    return sinyaller


# ---------------------------------------------------------------- raporlama


def _oran(kayitlar):
    """Zaman asimina ugrayanlar haric isabet orani + orneklem."""
    karar = [k for k in kayitlar if k["sonuc"] != "ZAMAN"]
    if not karar:
        return None, 0, len(kayitlar)
    kazanc = sum(1 for k in karar if k["sonuc"] == "KAZANC")
    return 100.0 * kazanc / len(karar), len(karar), len(kayitlar)


def _satir(etiket, kayitlar, taban=None):
    oran, n, toplam = _oran(kayitlar)
    if oran is None:
        return f"  {etiket:<28} {'-':>7}   {0:>4} sinyal"
    fark = ""
    if taban is not None:
        fark = f"  ({oran - taban:+.1f} puan)"
    guven = "" if n >= 30 else "   ⚠ orneklem kucuk"
    return (f"  {etiket:<28} {oran:>6.1f}%   {n:>4} karar "
            f"({toplam} sinyal){fark}{guven}")


def rapor(ad, sinyaller):
    print(f"\n{'=' * 74}\n{ad}  —  {len(sinyaller)} sinyal\n{'=' * 74}")
    if not sinyaller:
        print("  sinyal uretilmedi")
        return

    taban, n, _ = _oran(sinyaller)
    print(f"\nTABAN (tum sinyaller, filtre yok)")
    print(_satir("hepsi", sinyaller))
    if taban is None:
        return

    print(f"\nZAMAN KAPISI  — asil soru: kill zone filtresi ise yariyor mu?")
    for kapi in ("TETIK", "IZLE"):
        print(_satir(kapi, [s for s in sinyaller if s["kapi"] == kapi], taban))

    print(f"\nPENCERE BAZINDA")
    gruplar = defaultdict(list)
    for s in sinyaller:
        gruplar[s["pencere"]].append(s)
    for pen in sorted(gruplar, key=lambda p: -len(gruplar[p])):
        print(_satir(pen, gruplar[pen], taban))

    print(f"\nDURUM DAGILIMI  — ONAY esigi (70) pratikte tutuyor mu?")
    dgr = defaultdict(list)
    for s in sinyaller:
        dgr[s["durum"]].append(s)
    for d in sorted(dgr, key=lambda x: -len(dgr[x])):
        print(_satir(d, dgr[d], taban))

    print(f"\nSMT")
    uyumlu = [s for s in sinyaller if not s["veto"]]
    vetolu = [s for s in sinyaller if s["veto"]]
    smt_var = [s for s in sinyaller if s["smt"]]
    print(_satir("SMT uyusmazligi VAR", smt_var, taban))
    print(_satir("SMT vetosu uygulandi", vetolu, taban))

    print(f"\nGUNLUK BIAS UYUMU")
    uyum = [s for s in sinyaller
            if (s["yon"] == "long" and s["gunluk_bias"] == "BULLISH")
            or (s["yon"] == "short" and s["gunluk_bias"] == "BEARISH")]
    ters = [s for s in sinyaller
            if (s["yon"] == "long" and s["gunluk_bias"] == "BEARISH")
            or (s["yon"] == "short" and s["gunluk_bias"] == "BULLISH")]
    print(_satir("bias ile UYUMLU", uyum, taban))
    print(_satir("bias ile TERS", ters, taban))

    print(f"\nBIRLESIK FILTRE (tetik penceresi + veto yok + bias uyumlu)")
    birlesik = [s for s in uyum if s["kapi"] == "TETIK" and not s["veto"]]
    print(_satir("hepsi birden", birlesik, taban))


def main():
    hedefler = ["BTCUSDT", "NQ1!", "ASELS", "TUPRS", "BIMAS"]
    gun = 60
    if len(sys.argv) > 1:
        hedefler = [sys.argv[1]]
    if len(sys.argv) > 2:
        gun = int(sys.argv[2])

    print(f"Geri test — {gun} gun, 1 ATR stop / {HEDEF_R} ATR hedef, "
          f"{ILERI} bar zaman siniri")

    for ad in hedefler:
        try:
            bars = tarihsel_15m(ad, gun)
        except Exception as ex:
            print(f"\n{ad}: veri alinamadi ({type(ex).__name__}: {ex})")
            continue
        if not bars or len(bars) < ISINMA + 50:
            print(f"\n{ad}: yetersiz gecmis ({len(bars) if bars else 0} bar)")
            continue
        kor, kor_ad = korele_tarihsel(ad, gun)
        ilk = datetime.fromtimestamp(bars[0]["t"], timezone.utc)
        son = datetime.fromtimestamp(bars[-1]["t"], timezone.utc)
        print(f"\n{ad}: {len(bars)} bar  "
              f"({ilk:%Y-%m-%d} → {son:%Y-%m-%d})  korele={kor_ad or '-'}")
        rapor(ad, oynat(ad, bars, kor, kor_ad))

    print("\n" + "=" * 74)
    print("NOT: Bu bir strateji karnesi degil, FILTRE karnesidir. Sorusu su:\n"
          "     ayni sinyalleri sadece belirli saatlerde alsaydim ne olurdu?\n"
          "     Orneklem 30 kararin altindaysa fark tesadufi olabilir.")


if __name__ == "__main__":
    sys.exit(main())
