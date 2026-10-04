#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[DEVRE DISI 2026-10-04 — kripto artik Cloudflare Worker'da: C:\Users\USER\projects\kripto-sinyal-worker
 (SMC + Supurme+CISD+FVG + Supurme+IFVG, gercek 5 dk). Bu dosya yalniz elle `--kuru` denemesi icin duruyor.]

Kripto SMC izleyicisi — 7/24 otomatik tarama + Telegram sinyali.

Bilgisayardaki panelin (localhost:8765, kripto_jev_bot) Telegram sinyal
sisteminin BULUT kopyasi: ayni coinler, ayni motor, ayni aynalama. Bilgisayar
kapaliyken de calissin diye GitHub Actions'ta kosar.

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
LIMIT = 200              # panelle ayni: her periyottan son 200 mum

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


def _ayna(bars):
    return [{"t": b["t"], "o": -b["o"], "h": -b["l"], "l": -b["h"],
             "c": -b["c"], "v": b["v"]} for b in bars]


def degerlendir(ad, veri):
    """Bilgisayardaki panelin (kripto_jev_bot/smc/tarayici.py) yontemiyle
    BIREBIR ayni: gunluk yapi BEARISH ise fiyatlar aynalanir, BIST'in long
    motoru (smc_watch.degerlendir) kosar, sonuc gercek fiyatlara cevrilir.
    Kural tektir - short icin ayri motor yok."""
    yon = KUR.yon_belirle(veri)[0]
    short = yon == "BEARISH"
    r = W.degerlendir(ad, {k: _ayna(b) for k, b in veri.items()} if short else veri)
    r["taraf"] = "SHORT" if short else "LONG" if yon == "BULLISH" else "YOK"
    if short:
        r["fiyat"] = -r["fiyat"]
        r["durum"] = {"LONG_SINYAL": "SHORT_SINYAL",
                      "LONG_HAZIRLIK": "SHORT_HAZIRLIK"}.get(r["durum"], r["durum"])
        if r["plan"]:
            r["plan"] = dict(r["plan"], giris=-r["plan"]["giris"],
                             stop=-r["plan"]["stop"], hedef=-r["plan"]["hedef"])
        if r["bolge_fvg"]:
            z = r["bolge_fvg"]
            r["bolge_fvg"] = dict(z, alt=-z["ust"], ust=-z["alt"])
        if r["eq"] is not None:
            r["eq"] = -r["eq"]
            r["bolge"] = "PREMIUM" if r["bolge"] == "DISCOUNT" else "DISCOUNT"
        sp = r["supurme"] or {"lehte": [], "aleyhte": []}
        r["supurme"] = {k: [dict(x, seviye=-x["seviye"]) for x in v]
                        for k, v in sp.items()}
        r["yon_sebep"] = (r["yon_sebep"].replace("BULLISH", "@@")
                          .replace("BEARISH", "BULLISH").replace("@@", "BEARISH"))
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
