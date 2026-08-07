# -*- coding: utf-8 -*-
"""Durust egitim: ayarlari verinin ILK kisminda ara, HIC DOKUNMADIGIN
son kisminda test et.

Neden boyle?
  Gecmis veriye ayar uydurmak kolaydir. 100 farkli ayar denersen birkaci
  mutlaka %60 kazandirir - sansla. O ayari alip canliya koyarsan para
  kaybedersin, cunku gecmisin gurultusune uymus bir sayidir.

  Tek korunma yolu: ayari ARAMADIGIN veride test etmek. Egitim kisminda
  %60, test kisminda %33 cikiyorsa ayar uydurmadir. Ikisinde de benzer
  cikiyorsa gercek olabilir.

KOMISYON DAHILDIR. 5 dakikalik BTC barinda komisyon riskin buyuk kismini
yiyor; komisyonsuz geri test bu olcekte tamamen yanilticidir.

Kullanim:
    python egitim.py                  # 5dk, 60 gun, vadeli taker komisyonu
    python egitim.py 15 60 maker      # 15dk, 60 gun, maker komisyonu
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
ATR_N = 14
ILERI = 32                    # en fazla kac bar pozisyonda kalinir

KOMISYON = {                  # gidis-donus toplam oran
    "spot":  0.0020,          # Binance spot taker  %0.10 x2
    "taker": 0.0010,          # Binance vadeli taker %0.05 x2
    "maker": 0.0004,          # Binance vadeli maker %0.02 x2
    "yok":   0.0,             # karsilastirma icin
}


# ---------------------------------------------------------------- veri


def gun_barlari(tarih, periyot):
    os.makedirs(ONBELLEK, exist_ok=True)
    yol = os.path.join(ONBELLEK, f"{SEMBOL}-{periyot}-{tarih}.json")
    if os.path.exists(yol):
        return json.load(open(yol, encoding="utf-8"))
    isl = FP.arsiv_gun(SEMBOL, tarih)
    if not isl:
        return []
    ozetler = [FP.ozet(b) for b in FP.footprint_barlar(isl, periyot)]
    json.dump(ozetler, open(yol, "w", encoding="utf-8"))
    print(f"  {tarih}: {len(isl):>9,} islem -> {len(ozetler)} bar ({periyot}sn)")
    return ozetler


def veri_topla(gun, periyot):
    bugun = datetime.now(timezone.utc).date()
    hepsi = []
    for i in range(gun, 0, -1):
        hepsi.extend(gun_barlari((bugun - timedelta(days=i)).strftime("%Y-%m-%d"),
                                 periyot))
    hepsi.sort(key=lambda b: b["t"])
    return hepsi


# ---------------------------------------------------------------- olcum


def atr_serisi(barlar, n=ATR_N):
    out = [None] * len(barlar)
    trs = []
    for i in range(1, len(barlar)):
        o = barlar[i - 1]["c"]
        trs.append(max(barlar[i]["h"] - barlar[i]["l"],
                       abs(barlar[i]["h"] - o), abs(barlar[i]["l"] - o)))
        if len(trs) >= n:
            out[i] = sum(trs[-n:]) / n
    return out


def sonuc_R(barlar, i, yon, stop_mesafe, hedef_R):
    """Islemin R cinsinden BRUT sonucu (komisyon haric).

    Hedef gelirse +hedef_R, stop gelirse -1. Ikisi de gelmezse pozisyon
    zaman sinirinda piyasadan kapatilir ve gercek R yazilir - zaman asimini
    'yok saymak' sonuclari sisirir.
    Ayni barda ikisine de dokunulursa STOP sayilir (kotumser).
    """
    giris = barlar[i]["c"]
    yonu = 1 if yon == "long" else -1
    stop = giris - yonu * stop_mesafe
    hedef = giris + yonu * stop_mesafe * hedef_R
    for b in barlar[i + 1: i + 1 + ILERI]:
        if yonu > 0:
            if b["l"] <= stop:
                return -1.0
            if b["h"] >= hedef:
                return hedef_R
        else:
            if b["h"] >= stop:
                return -1.0
            if b["l"] <= hedef:
                return hedef_R
    son = barlar[min(i + ILERI, len(barlar) - 1)]["c"]
    return yonu * (son - giris) / stop_mesafe


def kosul_uret(barlar, atrs, gec=8):
    """Her bar icin footprint kosul degerlerini onceden hesaplar."""
    cvd, top = [], 0.0
    for b in barlar:
        top += b["delta"]
        cvd.append(top)
    out = []
    for i in range(len(barlar)):
        b = barlar[i]
        if i < max(ATR_N + 1, gec) or not atrs[i] or b["hacim"] <= 0:
            out.append(None)
            continue
        out.append({
            "do": b["delta_orani"],
            "emilim": b["emilim"],
            "cvd_bull": (b["l"] < min(x["l"] for x in barlar[i - gec:i])
                         and cvd[i] > min(cvd[i - gec:i])),
            "cvd_bear": (b["h"] > max(x["h"] for x in barlar[i - gec:i])
                         and cvd[i] < max(cvd[i - gec:i])),
            "fiyat": b["c"],
            "atr": atrs[i],
        })
    return out


def calistir(barlar, kosullar, p, komisyon_oran):
    """Bir ayar setini kosturur. Doner: (islem_sayisi, kazanma%, net_R_toplam)"""
    esik, emilim_sart, cvd_sart, hedef_R, stop_kat = p
    islemler = []
    for i, k in enumerate(kosullar):
        if k is None or i + 1 >= len(barlar):
            continue
        # Yon: agresyonu FADE et (olcum devamin calismadigini gosterdi)
        if k["do"] >= esik:
            yon = "short"
        elif k["do"] <= -esik:
            yon = "long"
        else:
            continue
        if emilim_sart:
            if yon == "short" and k["emilim"] != "alis_emildi":
                continue
            if yon == "long" and k["emilim"] != "satis_emildi":
                continue
        if cvd_sart:
            if yon == "short" and not k["cvd_bear"]:
                continue
            if yon == "long" and not k["cvd_bull"]:
                continue

        stop_mesafe = k["atr"] * stop_kat
        if stop_mesafe <= 0:
            continue
        brut = sonuc_R(barlar, i, yon, stop_mesafe, hedef_R)
        # Komisyon R cinsine cevrilir: maliyet / stop mesafesi
        komisyon_R = (k["fiyat"] * komisyon_oran) / stop_mesafe
        islemler.append(brut - komisyon_R)

    if not islemler:
        return 0, 0.0, 0.0
    kazanan = sum(1 for r in islemler if r > 0)
    return len(islemler), 100.0 * kazanan / len(islemler), sum(islemler)


# ---------------------------------------------------------------- egitim


IZGARA = [(e, em, cv, h, s)
          for e in (0.25, 0.35, 0.45, 0.55)
          for em in (False, True)
          for cv in (False, True)
          for h in (1.0, 1.5, 2.0, 3.0)
          for s in (1.0, 1.5)]


def ayar_metni(p):
    e, em, cv, h, s = p
    ek = []
    if em:
        ek.append("emilim sart")
    if cv:
        ek.append("CVD sart")
    return (f"delta>|{e:.0%}| fade, stop {s}xATR, hedef {h}R"
            + (("  [" + ", ".join(ek) + "]") if ek else ""))


def main():
    periyot_dk = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    gun = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    kom_ad = sys.argv[3] if len(sys.argv) > 3 else "taker"
    kom = KOMISYON[kom_ad]

    print(f"EGITIM — {SEMBOL}, {periyot_dk} dakikalik bar, {gun} gun")
    print(f"Komisyon: {kom_ad} (gidis-donus %{kom * 100:.2f})")
    print(f"Zaman siniri: {ILERI} bar\n")

    barlar = veri_topla(gun, periyot_dk * 60)
    if len(barlar) < 500:
        print("Yetersiz veri")
        return 1

    kesim = int(len(barlar) * 0.67)
    egitim, test = barlar[:kesim], barlar[kesim:]
    e_atr, t_atr = atr_serisi(egitim), atr_serisi(test)
    e_kos, t_kos = kosul_uret(egitim, e_atr), kosul_uret(test, t_atr)

    def tarih(b):
        return datetime.fromtimestamp(b["t"], timezone.utc).strftime("%Y-%m-%d")

    print(f"{len(barlar)} bar toplam")
    print(f"  EGITIM : {len(egitim):>6} bar  ({tarih(egitim[0])} → {tarih(egitim[-1])})")
    print(f"  TEST   : {len(test):>6} bar  ({tarih(test[0])} → {tarih(test[-1])})"
          f"   ← ayar ararken HIC BAKILMADI\n")

    # --- egitim kisminda tara
    sonuclar = []
    for p in IZGARA:
        n, kaz, netR = calistir(egitim, e_kos, p, kom)
        if n >= 40:                     # cok az islem ureten ayar guvenilmez
            sonuclar.append((netR / n, n, kaz, netR, p))
    if not sonuclar:
        print("Hicbir ayar yeterli islem uretmedi.")
        return 1
    sonuclar.sort(reverse=True)

    print("=" * 78)
    print("EGITIM KISMINDA EN IYI 5 AYAR")
    print("=" * 78)
    print(f"{'islem':>6} {'kazanma':>8} {'net R':>9} {'R/islem':>9}   ayar")
    for rpi, n, kaz, netR, p in sonuclar[:5]:
        print(f"{n:>6} {kaz:>7.1f}% {netR:>9.1f} {rpi:>9.3f}   {ayar_metni(p)}")

    # --- en iyisini TEST kisminda dogrula
    print("\n" + "=" * 78)
    print("AYNI AYARLAR, HIC GORULMEMIS TEST KISMINDA")
    print("=" * 78)
    print(f"{'islem':>6} {'kazanma':>8} {'net R':>9} {'R/islem':>9}   ayar")
    dusus = []
    for rpi, n, kaz, netR, p in sonuclar[:5]:
        tn, tkaz, tnet = calistir(test, t_kos, p, kom)
        trpi = tnet / tn if tn else 0.0
        print(f"{tn:>6} {tkaz:>7.1f}% {tnet:>9.1f} {trpi:>9.3f}   {ayar_metni(p)}")
        dusus.append((rpi, trpi))

    print("\n" + "=" * 78)
    print("YORUM")
    print("=" * 78)
    e_ort = sum(d[0] for d in dusus) / len(dusus)
    t_ort = sum(d[1] for d in dusus) / len(dusus)
    print(f"  Egitimde islem basina ortalama: {e_ort:+.3f} R")
    print(f"  Testte  islem basina ortalama: {t_ort:+.3f} R")
    if t_ort <= 0:
        print("\n  → Egitimde kazandiran ayarlar testte KAYBEDIYOR.")
        print("    Bu, ayarlarin gecmis gurultuye uydurulmus oldugu anlamina gelir.")
        print("    Bulunan sayilar canliya TASINMAZ.")
    elif t_ort < e_ort * 0.5:
        print("\n  → Testte kar var ama egitimin cok altinda. Kismen uydurma.")
    else:
        print("\n  → Test egitime yakin. Bu, uzerine gidilebilecek bir bulgudur.")
    print("\n  100 islemde beklenen net sonuc (test oranina gore): "
          f"{t_ort * 100:+.1f} R")
    print("  1R = riske ettigin tutar. +10 R, 100 islemde 10 birim kazanc demek.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
