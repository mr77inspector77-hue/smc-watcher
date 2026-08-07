# -*- coding: utf-8 -*-
"""Gorsel motor: durumu PNG olarak cizer ve Telegram'a fotograf gonderir.

TASARIM KARARI - onemli:
Grafik INSAN icindir, model icin degil. Seviyeler, supurmeler ve fazlar
deterministik Python motorunda SAYIDAN hesaplanir; buraya hazir gelir.
Hicbir asamada bir goruntu okunup sayi cikarilmaz. Bu, halusinasyon
yuzeyini sifirlar: grafikte gordugun her cizgi motorun hesapladigi
sayinin ta kendisidir.

matplotlib disinda bagimlilik yoktur (mplfinance kullanilmaz, mumlar
elle cizilir).
"""

import io
import json
import os
import urllib.request
from datetime import datetime, timezone

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                      # noqa: E402
from matplotlib.patches import Rectangle             # noqa: E402

from seanslar import bar_kill_zone, ts_to_tsi        # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

ARKA = "#0e1117"
IZGARA = "#22262f"
METIN = "#d6d9e0"
YESIL = "#26a69a"
KIRMIZI = "#ef5350"

# Kill zone / pencere kodu -> (renk, alfa)
KZ_RENK = {
    "ASYA":       ("#3d5afe", 0.10),
    "LONDRA":     ("#00bfa5", 0.13),
    "NY_AM":      ("#ffab00", 0.13),
    "SB_AM":      ("#ff6d00", 0.20),
    "SB_PM":      ("#ff6d00", 0.16),
    "OGLE":       ("#546e7a", 0.10),
    "ACILIS":     ("#ffab00", 0.15),
    "NY_ORTUSME": ("#00bfa5", 0.15),
    "KAPANIS":    ("#ff6d00", 0.15),
    "OLU":        ("#546e7a", 0.10),
}


def _mumlar(ax, bars):
    for i, b in enumerate(bars):
        renk = YESIL if b["c"] >= b["o"] else KIRMIZI
        ax.vlines(i, b["l"], b["h"], color=renk, linewidth=0.8, zorder=3)
        alt = min(b["o"], b["c"])
        boy = abs(b["c"] - b["o"]) or (b["h"] - b["l"]) * 0.002
        ax.add_patch(Rectangle((i - 0.32, alt), 0.64, boy, facecolor=renk,
                               edgecolor=renk, linewidth=0.5, zorder=4))


def _kz_boya(ax, bars, bist):
    """Kill zone / seans penceresi bantlarini arka plana boyar."""
    aktif_kod, bas = None, 0
    for i, b in enumerate(bars + [None]):
        kod = bar_kill_zone(b["t"], bist=bist) if b else None
        if kod != aktif_kod:
            if aktif_kod in KZ_RENK and i > bas:
                renk, alfa = KZ_RENK[aktif_kod]
                ax.axvspan(bas - 0.5, i - 0.5, color=renk, alpha=alfa, zorder=0)
                ax.text((bas + i) / 2 - 0.5, ax.get_ylim()[1], aktif_kod,
                        color=renk, fontsize=6.5, ha="center", va="top",
                        alpha=0.9, zorder=1)
            aktif_kod, bas = kod, i


def _seviye(ax, y, etiket, renk, n, stil="-", kalinlik=1.0, kuyruk=None):
    """Yatay seviye cizer. Etiket hemen basilmaz - `kuyruk`a yazilir ve
    en sonda cakismalar cozulerek topluca basilir."""
    ax.axhline(y, color=renk, linestyle=stil, linewidth=kalinlik, zorder=2,
               alpha=0.85)
    if kuyruk is not None:
        kuyruk.append([y, etiket, renk])


def _etiketleri_ciz(ax, kuyruk, n):
    """Sag kenardaki seviye etiketlerini ust uste binmeyecek sekilde dizer.

    Ayni fiyata denk gelen iki seviye (or. PDL ile DR dip) aksi halde
    birbirinin uzerine basiliyor ve ikisi de okunmaz hale geliyordu.
    """
    alt, ust = ax.get_ylim()
    asgari = (ust - alt) * 0.024          # iki etiket arasi asgari dikey bosluk
    kuyruk.sort(key=lambda x: x[0])
    yerlesik = []
    for y, metin, renk in kuyruk:
        hedef = y
        if yerlesik and hedef - yerlesik[-1] < asgari:
            hedef = yerlesik[-1] + asgari
        yerlesik.append(hedef)
        # Etiket kaydiysa cizgiye ince bir baglanti cizgisi cek
        if abs(hedef - y) > asgari * 0.3:
            ax.plot([n - 0.4, n + 0.5], [y, hedef], color=renk, linewidth=0.5,
                    alpha=0.55, zorder=5)
        ax.text(n + 0.6, hedef, metin, color=renk, fontsize=7,
                va="center", ha="left", zorder=6)


def grafik_ciz(r, bars, klasor, bist=False, bar_sayisi=110):
    """Durumu PNG'ye cizer, dosya yolunu dondurur.

    r: smc_watch.degerlendir() ciktisi (seviyeler, fvg, sweep, ict alanlari)
    """
    b = bars[-bar_sayisi:]
    n = len(b)
    if n < 10:
        return None

    fig, ax = plt.subplots(figsize=(11, 6.2), dpi=125)
    fig.patch.set_facecolor(ARKA)
    ax.set_facecolor(ARKA)

    _mumlar(ax, b)

    fiyatlar = [x["l"] for x in b] + [x["h"] for x in b]
    tab, taban = max(fiyatlar), min(fiyatlar)
    pay = (tab - taban) * 0.06 or 1.0
    ax.set_ylim(taban - pay, tab + pay)
    ax.set_xlim(-1, n + 15)          # sag bosluk: seviye etiketleri icin

    _kz_boya(ax, b, bist)

    # --- dealing range + premium/discount
    etiketler = []
    eq = r["eq"]
    ax.axhspan(eq, tab + pay, color="#ef5350", alpha=0.05, zorder=0)
    ax.axhspan(taban - pay, eq, color="#26a69a", alpha=0.05, zorder=0)
    _seviye(ax, r["aralik_tepe"], f"DR tepe {r['aralik_tepe']:.2f}", "#8892a6", n,
            kuyruk=etiketler)
    _seviye(ax, r["aralik_dip"], f"DR dip {r['aralik_dip']:.2f}", "#8892a6", n,
            kuyruk=etiketler)
    _seviye(ax, eq, f"EQ {eq:.2f}", "#c792ea", n, stil="--", kuyruk=etiketler)

    # --- FVG kutulari (doldurulmamis)
    ilk_i = len(bars) - n
    for g in (r.get("fvgler") or [])[-8:]:
        x = g["i"] - ilk_i
        if x < 0:
            x = 0
        renk = "#26a69a" if g["tip"] == "bull" else "#ef5350"
        ax.add_patch(Rectangle((x - 1, g["alt"]), (n - 0.5) - (x - 1),
                               g["ust"] - g["alt"], facecolor=renk, alpha=0.11,
                               edgecolor=renk, linewidth=0.4, zorder=1))

    # --- Asya araligi
    a = r.get("asya")
    if a:
        ax.axhspan(a["dip"], a["tepe"], color="#3d5afe", alpha=0.09, zorder=1)
        _seviye(ax, a["tepe"], f"Asya tepe {a['tepe']:.2f}", "#7c94ff", n,
                stil=":", kuyruk=etiketler)
        _seviye(ax, a["dip"], f"Asya dip {a['dip']:.2f}", "#7c94ff", n,
                stil=":", kuyruk=etiketler)

    # --- PO3 birikim kutusu
    p = r.get("po3") or {}
    if p.get("birikim"):
        k = p["birikim"]
        xs = [i for i, x in enumerate(b) if k["bas_ts"] <= x["t"] <= k["bit_ts"]]
        if xs:
            ax.add_patch(Rectangle((xs[0] - 0.5, k["dip"]),
                                   max(xs[-1] - xs[0], 1), k["tepe"] - k["dip"],
                                   facecolor="none", edgecolor="#ffd54f",
                                   linewidth=1.0, linestyle="--", zorder=5))
            ax.text(xs[0] - 0.5, k["tepe"], " PO3 birikim", color="#ffd54f",
                    fontsize=6.5, va="bottom", zorder=6)

    # --- gunluk/onceki gun seviyeleri
    gb = r.get("gunluk_bias") or {}
    if gb.get("pdh") is not None:
        pdh_al = " ✔alindi" if gb.get("pdh_alindi") else ""
        pdl_al = " ✔alindi" if gb.get("pdl_alindi") else ""
        _seviye(ax, gb["pdh"], f"PDH {gb['pdh']:.2f}{pdh_al}", "#ff8a65", n,
                stil="-.", kalinlik=0.8, kuyruk=etiketler)
        _seviye(ax, gb["pdl"], f"PDL {gb['pdl']:.2f}{pdl_al}", "#ff8a65", n,
                stil="-.", kalinlik=0.8, kuyruk=etiketler)

    # --- likidite supurmeleri
    s = r.get("sweeps") or {}
    if s.get("ssl"):
        _seviye(ax, s["ssl_seviye"], f"SSL supuruldu {s['ssl_seviye']:.2f}",
                "#26a69a", n, kalinlik=1.3, kuyruk=etiketler)
    if s.get("bsl"):
        _seviye(ax, s["bsl_seviye"], f"BSL supuruldu {s['bsl_seviye']:.2f}",
                "#ef5350", n, kalinlik=1.3, kuyruk=etiketler)

    # --- son fiyat
    _seviye(ax, r["fiyat"], f"{r['fiyat']:.2f}", "#ffffff", n, kalinlik=1.2,
            kuyruk=etiketler)
    _etiketleri_ciz(ax, etiketler, n)

    # --- eksenler
    adim = max(1, n // 9)
    yerler = list(range(0, n, adim))
    ax.set_xticks(yerler)
    ax.set_xticklabels([ts_to_tsi(b[i]["t"]).strftime("%d.%m\n%H:%M")
                        for i in yerler], fontsize=6.5)
    ax.tick_params(colors=METIN, labelsize=7)
    for kenar in ax.spines.values():
        kenar.set_color(IZGARA)
    ax.grid(color=IZGARA, linewidth=0.4, alpha=0.6, zorder=0)

    kz = (r.get("zaman") or {}).get("kz")
    kz_metin = kz["etiket"] if kz else "pencere disi"
    ax.set_title(
        f"{r['ad']}  ·  {r['durum'].replace('_', ' ')}  ·  skor {r['skor']}/100\n"
        f"{kz_metin}  ·  {(r.get('zaman') or {}).get('tsi', '')}  ·  "
        f"HTF {r['bias']}  ·  {r['bolge']}",
        color=METIN, fontsize=10, pad=12)
    fig.text(0.01, 0.015,
             f"kaynak: {r.get('kaynak', '-')}  ·  "
             f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC  ·  "
             "al/sat emri degildir",
             color="#5c6470", fontsize=6.5)

    fig.tight_layout()
    os.makedirs(klasor, exist_ok=True)
    yol = os.path.join(klasor, f"{r['ad'].replace('!', '')}.png")
    fig.savefig(yol, facecolor=ARKA)
    plt.close(fig)
    return yol


# ---------------------------------------------------------------- Telegram


def telegram_foto(cfg, yol, aciklama):
    """sendPhoto - multipart/form-data elle kurulur (requests bagimliligi yok)."""
    sinir = "----smcwatcher7f3a9"
    with open(yol, "rb") as f:
        icerik = f.read()

    tampon = io.BytesIO()

    def alan(ad, deger):
        tampon.write(f"--{sinir}\r\n".encode())
        tampon.write(f'Content-Disposition: form-data; name="{ad}"\r\n\r\n'.encode())
        tampon.write(str(deger).encode("utf-8"))
        tampon.write(b"\r\n")

    alan("chat_id", cfg["chat_id"])
    alan("caption", aciklama[:1024])
    alan("parse_mode", "HTML")

    tampon.write(f"--{sinir}\r\n".encode())
    tampon.write(('Content-Disposition: form-data; name="photo"; '
                  f'filename="{os.path.basename(yol)}"\r\n').encode())
    tampon.write(b"Content-Type: image/png\r\n\r\n")
    tampon.write(icerik)
    tampon.write(f"\r\n--{sinir}--\r\n".encode())

    istek = urllib.request.Request(
        f"https://api.telegram.org/bot{cfg['bot_token']}/sendPhoto",
        data=tampon.getvalue(),
        headers=dict(UA, **{"Content-Type":
                            f"multipart/form-data; boundary={sinir}"}))
    with urllib.request.urlopen(istek, timeout=40) as r:
        return json.load(r).get("ok", False)
