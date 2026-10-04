#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kripto SMC izleyicisi — 7/24 otomatik tarama + Telegram sinyali.

BIST izleyicisinin (smc_watch.py) kripto karsiligi. Ayni SMC motoru
(kurulum.py: yapi yonu, FVG, likidite, supurme) ve ayni skor kurallari;
farklar:
    Yon         LONG + SHORT (vadeli; asagi yapi short kurulumu demektir)
    Saat        7/24 — seans kontrolu yok
    Veri        Bybit -> OKX -> Binance (ilk calisan kaynak; GitHub'in
                ABD sunuculari bazi borsalarca engellenir)
    Periyot     Gunluk / 4H / 1H — 4H borsadan dogrudan cekilir (UTC
                00:00 hizali), 1H birlestirilmez

Telegram'a YALNIZ sinyal gider (kullanici karari 2026-10-03): durum
LONG_SINYAL'e ya da SHORT_SINYAL'e GECTIGINDE tek mesaj. Hazirlik /
kurulum yok durumlari yalniz log'a ve state dosyasina yazilir.

Coin listesi: kripto_liste.json. Elle tarama:  python kripto_watch.py --kuru
(mesaj atmaz, state yazmaz, her coinin durumunu basar)
"""

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kurulum as KUR    # noqa: E402   ortak SMC motoru
import smc_watch as W    # noqa: E402   skor agirliklari, telegram, bicim

BASE = os.path.dirname(os.path.abspath(__file__))
LISTE_PATH = os.path.join(BASE, "kripto_liste.json")
STATE_PATH = os.path.join(BASE, "kripto_state.json")
LOG_PATH = os.path.join(BASE, "kripto_watch.log")

ISTEK_ARASI_SN = 0.3
LIMIT = 300              # her periyottan son 300 mum (FVG 80, likidite 120 bar bakar)

# 1H barin bu yastan eskiyse veri bayat sayilir. Kriptoda seans yok:
# forming bar en fazla 60 dk; fazlasi kaynak sorunudur.
AZAMI_YAS_DK = 75

# Ayni coin sinyalden cikip 1-2 tur sonra geri girince ayni plani tekrar
# yollamasin: ayni yonde son sinyalden bu kadar saat gecmeden tekrar yok.
TEKRAR_BEKLEME_SA = 6


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------- veri


def _http(url):
    return KUR._http(url, timeout=20)


def _bybit(sym, aralik):
    iv = {"1h": "60", "4h": "240", "1d": "D"}[aralik]
    son = None
    for host in ("api.bybit.com", "api.bytick.com"):
        try:
            d = _http(f"https://{host}/v5/market/kline?category=linear"
                      f"&symbol={sym}&interval={iv}&limit={LIMIT}")
            break
        except Exception as ex:
            son = ex
    else:
        raise son
    if d.get("retCode") != 0:
        raise RuntimeError(f"bybit {d.get('retMsg')}")
    rows = d["result"]["list"]                     # yeniden eskiye
    return [{"t": int(r[0]) // 1000, "o": float(r[1]), "h": float(r[2]),
             "l": float(r[3]), "c": float(r[4]), "v": float(r[5])}
            for r in reversed(rows)]


def _okx(sym, aralik):
    bar = {"1h": "1H", "4h": "4H", "1d": "1Dutc"}[aralik]
    d = _http(f"https://www.okx.com/api/v5/market/candles?instId={sym}"
              f"&bar={bar}&limit={LIMIT}")
    if d.get("code") != "0":
        raise RuntimeError(f"okx {d.get('msg')}")
    return [{"t": int(r[0]) // 1000, "o": float(r[1]), "h": float(r[2]),
             "l": float(r[3]), "c": float(r[4]), "v": float(r[5])}
            for r in reversed(d["data"])]


def _binance(sym, aralik):
    # Yalniz piyasa verisi sunan ayna; spot fiyat (vadeliyle fark ihmal edilir).
    rows = _http(f"https://data-api.binance.vision/api/v3/klines?symbol={sym}"
                 f"&interval={aralik}&limit={LIMIT}")
    return [{"t": int(r[0]) // 1000, "o": float(r[1]), "h": float(r[2]),
             "l": float(r[3]), "c": float(r[4]), "v": float(r[5])}
            for r in rows]


KAYNAKLAR = (("Bybit", "bybit", _bybit), ("OKX", "okx", _okx),
             ("Binance", "binance", _binance))


def veri_cek(coin):
    """Ilk tam veri veren kaynaktan {"1d","4h","1h"} doner + kaynak adi."""
    hatalar = []
    for ad, anahtar, fn in KAYNAKLAR:
        sym = coin.get(anahtar)
        if not sym:
            continue
        try:
            veri = {a: fn(sym, a) for a in ("1d", "4h", "1h")}
            if min(len(v) for v in veri.values()) < 60:
                raise RuntimeError("kisa veri")
            return veri, ad
        except Exception as ex:
            hatalar.append(f"{ad}: {type(ex).__name__} {str(ex)[:60]}")
    raise RuntimeError(" | ".join(hatalar) or "kaynak yok")


# ---------------------------------------------------------------- kurulum


def kurulum_kur(ad, veri):
    """kurulum.kurulum_kur'un iki yonlu hali. Long tarafi birebir aynidir;
    short tarafi aynanin tersidir: bolge fiyatin USTUNDE bear FVG, stop
    bolgenin ustunde, hedef asagidaki alinmamis likidite."""
    fiyat = veri["1h"][-1]["c"]
    a1 = KUR.atr(veri["1h"])
    yon, yon_sebep, yon_guc = KUR.yon_belirle(veri)
    sonuc = {"ad": ad, "fiyat": fiyat, "yon": yon, "yon_sebep": yon_sebep,
             "yon_guc": yon_guc, "atr_1h": a1, "durum": "YON YOK",
             "bolge": None, "plan": None, "supurme": None}
    if yon == "RANGE" or not a1:
        return sonuc

    long_mu = yon == "BULLISH"
    sonuc["supurme"] = KUR.supurme_katmani(veri, yon)

    zonlar = KUR.bolgeler(veri, yon, a1)
    if not zonlar:
        sonuc["durum"] = "BOLGE YOK (yeterli genislikte)"
        return sonuc
    z = zonlar[0]
    sonuc["bolge"] = z
    ustler, altlar = KUR.likidite_seviyeleri(veri["4h"])

    if long_mu:
        giris = min(z["ust"], fiyat)
        stop = z["alt"] - a1 * KUR.STOP_PAYI
        aday = [s for s in ustler if s > giris]          # artan sirada
    else:
        giris = max(z["alt"], fiyat)
        stop = z["ust"] + a1 * KUR.STOP_PAYI
        aday = [s for s in altlar if s < giris]          # azalan sirada
    hedef = aday[0] if aday else None
    if hedef is None:
        sonuc["durum"] = "HEDEF YOK"
        return sonuc

    risk = abs(giris - stop)
    taban = a1 * KUR.ASGARI_STOP
    genisletildi = risk < taban
    if genisletildi:
        risk = taban
        stop = giris - risk if long_mu else giris + risk

    odul = abs(hedef - giris)
    rr = odul / risk if risk > 0 else 0
    sonuc["plan"] = {"giris": giris, "stop": stop, "hedef": hedef,
                     "risk": risk, "odul": odul, "rr": rr,
                     "stop_genisletildi": genisletildi}
    if rr < KUR.ASGARI_RR:
        sonuc["durum"] = f"RR YETERSIZ ({rr:.1f})"
        return sonuc
    if rr > KUR.AZAMI_RR:
        sonuc["durum"] = f"RR SUPHELI ({rr:.1f}) — hedef fazla uzak"
        return sonuc

    pay = a1 * KUR.BOLGE_PAYI
    icinde = (z["alt"] - pay) <= fiyat <= (z["ust"] + pay)
    sonuc["durum"] = "BOLGEDE — GIRIS SARTLARI TAMAM" if icinde else "BEKLEMEDE"
    return sonuc


def skorla(kur, fiyat, eq, long_mu):
    """smc_watch.skorla ile ayni agirliklar; konum katmani yone gore:
    long DISCOUNT'ta, short PREMIUM'da puan alir."""
    A = W.AGIRLIK
    d = {"yon": A["yon"] * {"uyumlu": 1.0, "notr": 0.75,
                            "zayif": 0.4}.get(kur.get("yon_guc"), 0.0)}
    if kur["bolge"] and kur["durum"].startswith("BOLGEDE"):
        d["bolge"] = A["bolge"]
    elif kur["bolge"]:
        d["bolge"] = A["bolge"] * 0.5
    else:
        d["bolge"] = 0
    p = kur.get("plan")
    rr = p["rr"] if p else 0
    d["rr"] = A["rr"] if rr >= 3.0 else (A["rr"] * 0.66 if rr >= KUR.ASGARI_RR else 0)
    sp = kur.get("supurme") or {"lehte": [], "aleyhte": []}
    if sp["lehte"]:
        g = sp["lehte"][0]["guc"]
        if sp["aleyhte"]:
            g *= W.ALEYHTE_CARPAN
        d["supurme"] = A["supurme"] * g
    else:
        d["supurme"] = 0
    if eq is None:
        d["pd"] = 0
    else:
        d["pd"] = A["pd"] if ((fiyat < eq) if long_mu else (fiyat > eq)) else 0
    return round(sum(d.values())), {k: (round(v), A[k]) for k, v in d.items()}


def degerlendir(ad, veri):
    kur = kurulum_kur(ad, veri)
    fiyat = kur["fiyat"]
    if len(veri["1d"]) >= 40:
        hi, lo, eq = W.dealing_range(veri["1d"], 40)
    else:
        hi = lo = eq = None
    long_mu = kur["yon"] == "BULLISH"
    taraf = "LONG" if long_mu else "SHORT"
    r = {
        "ad": ad, "fiyat": fiyat, "yon_smc": kur["yon"], "taraf": taraf,
        "yon_sebep": kur["yon_sebep"], "kur_durum": kur["durum"],
        "bolge_fvg": kur["bolge"], "plan": kur["plan"],
        "supurme": kur["supurme"], "aralik_tepe": hi, "aralik_dip": lo,
        "eq": eq, "detay": {}, "skor": 0,
        "bolge": ("-" if eq is None else ("DISCOUNT" if fiyat < eq else "PREMIUM")),
        "zaman_utc": datetime.fromtimestamp(veri["1h"][-1]["t"], timezone.utc)
                             .strftime("%Y-%m-%d %H:%M UTC"),
    }
    if kur["yon"] == "RANGE":
        r["durum"] = "NOTR"
        return r
    skor, detay = skorla(kur, fiyat, eq, long_mu)
    r["skor"], r["detay"] = skor, detay
    p = kur["plan"]
    alinabilir = bool(p) and p["rr"] >= KUR.ASGARI_RR and \
        kur["durum"].startswith("BOLGEDE")
    if skor >= W.ESIK_ONAY and alinabilir:
        r["durum"] = f"{taraf}_SINYAL"
    elif skor >= W.ESIK_HAZIRLIK:
        r["durum"] = f"{taraf}_HAZIRLIK"
    else:
        r["durum"] = "NOTR"
    return r


# ---------------------------------------------------------------- mesaj


def fmt(x):
    """Kripto fiyatlari 0,0001'den 100.000'e uzanir: anlamli basamak."""
    if x is None:
        return "-"
    a = abs(x)
    ond = 2 if a >= 100 else 3 if a >= 10 else 4 if a >= 1 else 5 if a >= 0.1 else 6
    return W.fmt(x, ond)


def mesaj_olustur(r, kaynak):
    long_mu = r["taraf"] == "LONG"
    p, d, z = r["plan"], r["detay"], r["bolge_fvg"]
    e = "🟢" if long_mu else "🔴"
    s = [f"{e} <b>{r['ad']}/USDT — {r['taraf']} SİNYAL</b>",
         f"Fiyat <b>{fmt(r['fiyat'])}</b>   ·   Skor <b>{r['skor']}</b>/100", "",
         "<b>━━━━━ İŞLEM PLANI ━━━━━</b>",
         f"{e} <b>GİRİŞ  {fmt(p['giris'])}</b>",
         f"🛑 <b>STOP   {fmt(p['stop'])}</b>   {W._yuzde(p['stop'], p['giris'])}",
         f"🎯 <b>HEDEF  {fmt(p['hedef'])}</b>   {W._yuzde(p['hedef'], p['giris'])}",
         "", f"⚖️ R:R <b>1 : {W._ondalik(p['rr'])}</b>"]
    if p.get("stop_genisletildi"):
        s.append("<i>Stop gürültü tabanına genişletildi.</i>")

    def isaret(k):
        a, m = d[k]
        return "✅" if a == m else ("🟡" if a else "⛔")

    def puan(k):
        return f"({d[k][0]}/{d[k][1]})"

    s += ["", "<b>━━━━━ NEDEN ━━━━━</b>",
          f"{isaret('yon')} Yön: {r['yon_sebep']} {puan('yon')}",
          f"{isaret('bolge')} Bölge: {z['periyot']} FVG "
          f"{fmt(z['alt'])}–{fmt(z['ust'])}, fiyat İÇİNDE {puan('bolge')}",
          f"{isaret('rr')} R:R {W._ondalik(p['rr'])} {puan('rr')}"]
    sp = r["supurme"] or {"lehte": [], "aleyhte": []}
    ne = "dip" if long_mu else "tepe"
    if sp["lehte"]:
        k = sp["lehte"][0]
        s.append(f"{isaret('supurme')} Likidite: {k['periyot']} {ne} "
                 f"süpürüldü {fmt(k['seviye'])} ({k['bar_once']} bar önce) "
                 f"{puan('supurme')}")
    else:
        s.append(f"⛔ Likidite: lehte süpürme yok {puan('supurme')}")
    for k in sp["aleyhte"]:
        s.append(f"⚠️ Ters yönde {k['periyot']} "
                 f"{'tepe' if long_mu else 'dip'} süpürüldü {fmt(k['seviye'])}")
    s.append(f"{isaret('pd')} Konum: {r['bolge']} (istenen "
             f"{'DISCOUNT' if long_mu else 'PREMIUM'}) {puan('pd')}")
    s += ["", f"<i>{r['zaman_utc']} · {kaynak}</i>",
          "<i>Bu bir al/sat emri değildir — şartların durumudur.</i>"]
    return "\n".join(s)


# ---------------------------------------------------------------- ana akis


def _json_oku(yol, bos):
    try:
        with open(yol, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return bos


def main():
    kuru = "--kuru" in sys.argv
    if os.environ.get("SMC_TEST_MESAJI", "").lower() == "true":
        liste = [c["coin"] for c in _json_oku(LISTE_PATH, {}).get("coinler", [])]
        ok = W.telegram_gonder("🔧 <b>Kripto SMC izleyici — bağlantı testi</b>\n\n"
                               f"Takip: <b>{', '.join(liste)}</b>\n"
                               "Günlük / 4H / 1H · LONG + SHORT · 7/24\n\n"
                               "<i>Bu bir sinyal değildir.</i>")
        log(f"TEST MESAJI: {'OK' if ok else 'HATA'}")
        return 0 if ok else 1

    coinler = _json_oku(LISTE_PATH, {}).get("coinler", [])
    state = _json_oku(STATE_PATH, {})
    simdi = time.time()
    basarili = 0

    for sira, coin in enumerate(coinler):
        ad = coin["coin"]
        if sira:
            time.sleep(ISTEK_ARASI_SN)
        onceki = state.get(ad, {})
        try:
            veri, kaynak = veri_cek(coin)
        except Exception as ex:
            log(f"{ad}: VERI ALINAMADI ({ex})")
            onceki["veri_sorunu"] = True
            state[ad] = onceki
            continue
        yas = (simdi - veri["1h"][-1]["t"]) / 60.0
        if yas > AZAMI_YAS_DK:
            log(f"{ad}: veri bayat ({yas:.0f} dk, {kaynak}) - atlandi")
            continue
        basarili += 1

        r = degerlendir(ad, veri)
        eski = onceki.get("durum", "BASLANGIC")
        son_sinyal = onceki.get("son_sinyal") or {}

        if kuru:
            p = r["plan"]
            print(f"{ad:5} {kaynak:7} {r['durum']:14} skor {r['skor']:3}  "
                  f"{r['kur_durum'][:34]:34}"
                  + (f" giris {fmt(p['giris'])} stop {fmt(p['stop'])} "
                     f"hedef {fmt(p['hedef'])} rr {p['rr']:.1f}" if p else ""))
            continue

        sinyal = r["durum"].endswith("_SINYAL")
        yeni = sinyal and r["durum"] != eski
        if yeni and son_sinyal.get("durum") == r["durum"] and \
                simdi - son_sinyal.get("ts", 0) < TEKRAR_BEKLEME_SA * 3600:
            log(f"{ad}: {r['durum']} tekrar - {TEKRAR_BEKLEME_SA} sa dolmadi, mesaj yok")
            yeni = False
        if yeni:
            try:
                ok = W.telegram_gonder(mesaj_olustur(r, kaynak))
                log(f"{ad}: {eski} -> {r['durum']} (skor {r['skor']}) "
                    f"TELEGRAM={'OK' if ok else 'HATA'}")
                son_sinyal = {"durum": r["durum"], "ts": simdi}
            except Exception as ex:
                log(f"{ad}: Telegram gonderim hatasi: {ex}")
        elif r["durum"] != eski:
            log(f"{ad}: {eski} -> {r['durum']} (skor {r['skor']}) - sinyal degil")

        p = r["plan"]
        state[ad] = {
            "veri_sorunu": False, "kaynak": kaynak, "durum": r["durum"],
            "skor": r["skor"], "fiyat": r["fiyat"], "yon": r["yon_smc"],
            "kurulum": r["kur_durum"],
            "plan": ({"giris": p["giris"], "stop": p["stop"],
                      "hedef": p["hedef"], "rr": round(p["rr"], 2)} if p else None),
            "son_sinyal": son_sinyal or None,
            "zaman_utc": r["zaman_utc"],
            "guncelleme": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        }

    log(f"tur bitti: {basarili}/{len(coinler)} coin tarandi")
    if not kuru:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    # Hicbir coin taranamadiysa is KIRMIZI bitsin: GitHub e-posta atar,
    # sistemin kor kaldigi Telegram'a mesaj olmadan da gorulur.
    return 0 if basarili or not coinler else 2


if __name__ == "__main__":
    sys.exit(main())
