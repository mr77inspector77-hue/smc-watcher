#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SMC Watcher - TEK YONTEM. 5 enstruman icin otomatik takip + Telegram.
(NQ1!, BTCUSDT, ASELS, TUPRS, BIMAS)

Bu dosya artik iki isi birlestirir:
  - YAPIYI kurulum.py motoru kurar (yon -> bolge -> supurme -> plan -> tetik).
    Ayni motor elle `python kurulum.py` ile de calisir; kural tektir.
  - SKORU ve TELEGRAM'i bu dosya yapar.

Katman politikasi (kurulum.py ile ayni, orada tanimli):
    yon      BTC + NQ long/short  ·  BIST YALNIZ LONG
    derin    supurme + footprint SIMDILIK YALNIZ BTC

Skor 0-100'dur ve enstrumana gore RENORMALIZE edilir: NQ/BIST'te supurme
ve footprint katmanlari kapali oldugu icin o iki agirlik toplamdan dusulur,
kalanlar 100'e olceklenir. Boylece 70/45 esikleri her enstrumanda ayni
anlami tasir - kapali katman yuzunden hisse skoru bastan sakat kalmaz.

ONAY icin skor yetmez: somut bir plan (giris/stop/hedef) ve R:R >= esik
sarttir. Skor "ne kadar guzel", plan "alinabilir mi" sorusunu yanitlar.

BTC'de derin katmanlarin payi %25: supurme/footprint teyidi hic yokken
azami 75 alinir - ONAY mumkun ama ancak yapinin geri kalani kusursuzsa.
Veto degil, agir handikap. Ikisi de ikili degil DERECELIDIR - supurmede
periyot/tazelik/derinlik/havuz, footprint'te emilim/CVD/son bar/dengesizlik
ayri ayri tartilir.

15 dakikada bir calisir (cron veya Windows zamanlanmis gorevi).
Veri: veri_kaynaklari.py uzerinden COKLU KAYNAK, otomatik yedege gecisli.
Durum degistiginde Telegram'a mesaj atar. Degismezse sessiz kalir.
Hicbir kaynak veri veremezse "kor kaldim" uyarisi gonderir.
"""

import json
import os
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from veri_kaynaklari import bar_cek, yahoo_15m, yas_dk  # noqa: E402
import kurulum as KUR  # noqa: E402   ortak analiz motoru

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "telegram_config.json")
STATE_PATH = os.path.join(BASE, "smc_state_auto.json")
LOG_PATH = os.path.join(BASE, "smc_watch.log")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# Skor esikleri
ESIK_ONAY = 70
ESIK_HAZIRLIK = 45

# Katman agirliklari. DERIN olanlar yalniz BTC'de sayilir; digerlerinde
# toplamdan dusulup kalanlar 100'e olceklenir (renormalizasyon).
#
# BTC'de derin katmanlarin payi %25 (once %20 idi, %32'ye cikarildi, sonra
# kullanici istegiyle yumusatildi). Kapi artik KAPALI degil SIKI: derin
# teyit hic yokken azami 75 alinir - ONAY esigini (70) gecer, ama ancak
# yapinin geri kalani KUSURSUZSA. Yon "zayif" cikarsa 63'e duser ve
# gecemez. Yani footprint/supurme yoklugu tek basina veto degil, ciddi bir
# handikaptir. Diger enstrumanlarda o iki katman olmadigi icin havuz 75'e
# duser ve kalanlar 100'e olceklenir.
AGIRLIK = {
    "yon": 24,        # haftalik + gunluk yapi uyumu
    "bolge": 19,      # yonle uyumlu FVG ve fiyatin ona varmis olmasi
    "rr": 13,         # planin risk/odul kalitesi
    "pd": 9,          # long'da discount / short'ta premium tarafta olmak
    "kirilim": 10,    # 15dk yapi kirilimi teyidi
    "supurme": 13,    # DERIN - likidite supurmesi (dereceli)
    "tetik": 12,      # DERIN - footprint order flow teyidi (dereceli)
}
DERIN_KATMANLAR = ("supurme", "tetik")

# Aleyhte supurme cezasi: biz long bakarken tepe supuruldiyse yukaridaki
# yakit zaten harcanmis demektir. Lehteki supurmeyi silmez, degerini kirar.
ALEYHTE_CARPAN = 0.55

# Footprint'i fiyat bolgeye bu kadar ATR yaklastiginda sormaya basla.
# Sadece "bolgedeyken" sormak, donusu tetik kurulduktan sonra gormek demekti.
TETIK_YAKINLIK_ATR = 1.0

# ---------------------------------------------------------------- yardimcilar


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def http_json(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


# ---------------------------------------------------------------- veri cekme


def fetch_binance(symbol="BTCUSDT", interval="15m", limit=200):
    url = (
        "https://api.binance.com/api/v3/klines"
        f"?symbol={symbol}&interval={interval}&limit={limit}"
    )
    raw = http_json(url)
    return [
        {
            "t": int(k[0]) // 1000,
            "o": float(k[1]),
            "h": float(k[2]),
            "l": float(k[3]),
            "c": float(k[4]),
            "v": float(k[5]),
        }
        for k in raw
    ]


def fetch_yahoo(symbol="NQ%3DF", interval="15m", rng="5d"):
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?interval={interval}&range={rng}"
    )
    d = http_json(url)
    r = d["chart"]["result"][0]
    ts = r["timestamp"]
    q = r["indicators"]["quote"][0]
    bars = []
    for i, t in enumerate(ts):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c):
            continue
        v = q.get("volume", [None] * len(ts))[i] or 0
        bars.append({"t": int(t), "o": o, "h": h, "l": l, "c": c, "v": float(v)})
    return bars


def resample_to_1h(bars15):
    """15dk barlari 1 saatlige toplar (saat basi gruplama)."""
    out = {}
    for b in bars15:
        key = b["t"] - (b["t"] % 3600)
        if key not in out:
            out[key] = {"t": key, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"]}
        else:
            g = out[key]
            g["h"] = max(g["h"], b["h"])
            g["l"] = min(g["l"], b["l"])
            g["c"] = b["c"]
            g["v"] += b["v"]
    return [out[k] for k in sorted(out)]


# ---------------------------------------------------------------- SMC motoru


def pivots(bars, n=2):
    """Fractal swing noktalari. (indeks, fiyat) listeleri dondurur."""
    highs, lows = [], []
    for i in range(n, len(bars) - n):
        win = bars[i - n : i + n + 1]
        if bars[i]["h"] == max(b["h"] for b in win):
            highs.append((i, bars[i]["h"]))
        if bars[i]["l"] == min(b["l"] for b in win):
            lows.append((i, bars[i]["l"]))
    return highs, lows


def htf_bias(bars1h):
    """1 saatlik yapidan HTF bias: BULLISH / BEARISH / RANGE"""
    ph, pl = pivots(bars1h, n=2)
    if len(ph) < 2 or len(pl) < 2:
        return "RANGE"
    hh = ph[-1][1] > ph[-2][1]
    hl = pl[-1][1] > pl[-2][1]
    lh = ph[-1][1] < ph[-2][1]
    ll = pl[-1][1] < pl[-2][1]
    if hh and hl:
        return "BULLISH"
    if lh and ll:
        return "BEARISH"
    return "RANGE"


def find_fvgs(bars, lookback=60):
    """Doldurulmamis Fair Value Gap'ler. tip: 'bull' | 'bear'"""
    out = []
    start = max(2, len(bars) - lookback)
    for i in range(start, len(bars)):
        c1, c3 = bars[i - 2], bars[i]
        if c1["h"] < c3["l"]:
            out.append({"tip": "bull", "alt": c1["h"], "ust": c3["l"], "i": i})
        elif c1["l"] > c3["h"]:
            out.append({"tip": "bear", "alt": c3["h"], "ust": c1["l"], "i": i})
    # tamamen doldurulmus olanlari ele
    canli = []
    for g in out:
        sonrasi = bars[g["i"] + 1 :]
        if not sonrasi:
            canli.append(g)
            continue
        if g["tip"] == "bull":
            if min(b["l"] for b in sonrasi) > g["alt"]:
                canli.append(g)
        else:
            if max(b["h"] for b in sonrasi) < g["ust"]:
                canli.append(g)
    return canli


def find_sweeps(bars, pencere=10, referans=30):
    """Son 'pencere' barda likidite supurmesi oldu mu."""
    if len(bars) < pencere + referans:
        return {"ssl": False, "bsl": False, "ssl_seviye": None, "bsl_seviye": None}
    ref = bars[-(pencere + referans) : -pencere]
    son = bars[-pencere:]
    ref_dip = min(b["l"] for b in ref)
    ref_tepe = max(b["h"] for b in ref)
    ssl = any(b["l"] < ref_dip and b["c"] > ref_dip for b in son)
    bsl = any(b["h"] > ref_tepe and b["c"] < ref_tepe for b in son)
    return {
        "ssl": ssl,
        "bsl": bsl,
        "ssl_seviye": ref_dip if ssl else None,
        "bsl_seviye": ref_tepe if bsl else None,
    }


def structure_break(bars, n=2):
    """15dk yapi kirilimi yonu: 'bull' | 'bear' | None"""
    ph, pl = pivots(bars, n=n)
    if not ph or not pl:
        return None, None, None
    son_kapanis = bars[-1]["c"]
    son_tepe = ph[-1][1]
    son_dip = pl[-1][1]
    if son_kapanis > son_tepe:
        return "bull", son_tepe, son_dip
    if son_kapanis < son_dip:
        return "bear", son_tepe, son_dip
    return None, son_tepe, son_dip


def dealing_range(bars, bar_sayisi=96):
    son = bars[-bar_sayisi:]
    hi = max(b["h"] for b in son)
    lo = min(b["l"] for b in son)
    return hi, lo, (hi + lo) / 2.0


def skorla(kur, yon, fiyat, eq, kirilim, tetik, derin):
    """kurulum.py ciktisini 0-100 skora cevirir.

    Kapali katmanlar (NQ/BIST'te supurme + footprint) toplamdan DUSULUR ve
    kalan agirliklar 100'e olceklenir. Aksi halde hisse skoru, hic acilmamis
    bir kapidan puan alamadigi icin bastan 20 puan geride baslardi.

    Doner: (skor, detay, azami) - detay her katman icin (alinan, azami).
    """
    d = {}

    # 1) YON — haftalik + gunluk uyumu
    guc = kur.get("yon_guc")
    d["yon"] = AGIRLIK["yon"] if guc == "uyumlu" else (
        AGIRLIK["yon"] * 0.5 if guc == "zayif" else 0)

    # 2) BOLGE — bolge var mi, fiyat ona VARDI mi
    if kur["bolge"] and kur["durum"].startswith("BOLGEDE"):
        d["bolge"] = AGIRLIK["bolge"]
    elif kur["bolge"]:
        d["bolge"] = AGIRLIK["bolge"] * 0.5      # bolge var ama fiyat gelmedi
    else:
        d["bolge"] = 0

    # 3) R:R — planin kalitesi
    p = kur.get("plan")
    rr = p["rr"] if p else 0
    if rr >= 3.0:
        d["rr"] = AGIRLIK["rr"]
    elif rr >= KUR.ASGARI_RR:
        d["rr"] = AGIRLIK["rr"] * 0.66
    else:
        d["rr"] = 0

    # 4) Premium / Discount dogru taraf
    if eq is None:
        d["pd"] = 0
    elif yon == "long":
        d["pd"] = AGIRLIK["pd"] if fiyat < eq else 0
    else:
        d["pd"] = AGIRLIK["pd"] if fiyat > eq else 0

    # 5) 15dk yapi kirilimi teyidi
    uyumlu = (yon == "long" and kirilim == "bull") or \
             (yon == "short" and kirilim == "bear")
    d["kirilim"] = AGIRLIK["kirilim"] if uyumlu else 0

    if derin:
        # 6) Likidite supurmesi — DERECELI. En guclu lehte supurme sayilir,
        #    aleyhte supurme varsa deger kirilir.
        sp = kur.get("supurme") or {"lehte": [], "aleyhte": []}
        if sp["lehte"]:
            g = sp["lehte"][0]["guc"]
            if sp["aleyhte"]:
                g *= ALEYHTE_CARPAN
            d["supurme"] = AGIRLIK["supurme"] * g
        else:
            d["supurme"] = 0
        # 7) Footprint — DERECELI (emilim + CVD + son bar + dengesizlik)
        #    Tik verisi hic alinamadiysa katman HAVUZDAN CIKAR. Sifir vermek
        #    olculemeyeni "teyit yok" saymak olurdu; ortam kaynakli bir
        #    eksikligin bedelini kuruluma odetmeyiz.
        if tetik is not None and tetik.get("durum") != "ok":
            pass
        else:
            d["tetik"] = AGIRLIK["tetik"] * KUR.tetik_gucu(tetik, kur["yon"])

    azami = sum(AGIRLIK[k] for k in d)
    ham = sum(d.values())
    skor = round(ham * 100.0 / azami) if azami else 0
    detay = {k: (round(v), AGIRLIK[k]) for k, v in d.items()}
    return skor, detay, azami


def degerlendir(ad, bars15, htf):
    """Tek yontem: yapiyi kurulum.py kurar, skoru burasi olcer.

    bars15 yalniz TAZELIK ve 15dk yapi kirilimi icin kullanilir. Seviyeler
    (giris/stop/hedef) her zaman HTF veriden gelir - 15dk kaynak vekil
    (orn. QQQ) oldugunda bile plan gercek enstrumanin fiyatiyla kurulur.
    """
    kur = KUR.kurulum_kur(ad, htf)
    fiyat = kur["fiyat"]
    derin = kur["derin"]
    kirilim, son_tepe, son_dip = structure_break(bars15)

    # Gunluk dealing range -> premium / discount
    gunluk = htf.get("1d") or []
    if len(gunluk) >= 40:
        hi, lo, eq = dealing_range(gunluk, 40)
    else:
        hi = lo = eq = None

    ortak = {
        "ad": ad, "fiyat": fiyat, "yon_smc": kur["yon"],
        "yon_sebep": kur["yon_sebep"], "kur_durum": kur["durum"],
        "bolge_fvg": kur["bolge"], "plan": kur["plan"],
        "supurme": kur["supurme"], "derin": derin,
        "aralik_tepe": hi, "aralik_dip": lo, "eq": eq,
        "bolge": ("-" if eq is None else ("PREMIUM" if fiyat > eq else "DISCOUNT")),
        "kirilim": kirilim, "son_tepe": son_tepe, "son_dip": son_dip,
        "tetik": None, "detay": {}, "skor": 0, "yon": "-",
        "zaman_utc": datetime.fromtimestamp(bars15[-1]["t"], timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"),
    }

    # BIST'te asagi yon: bu bir short sinyali degil, "long sartlari yok"tur.
    if kur["durum"].startswith("SHORT YON"):
        ortak["durum"] = "LONG_YOK"
        return ortak
    if kur["yon"] == "RANGE":
        ortak["durum"] = "NOTR"
        return ortak

    yon = "long" if kur["yon"] == "BULLISH" else "short"
    ortak["yon"] = yon

    # Footprint pahali bir cagri: yalniz BTC'de sorulur. Fiyat bolgeye
    # YAKLASIRKEN de sorulur - donusu ancak tetik kurulduktan sonra gormek
    # icin beklemek, order flow'un erken uyari degerini israf etmekti.
    tetik = None
    z = kur.get("bolge")
    a1 = kur.get("atr_1h") or 0
    yakin = bool(z) and a1 > 0 and z["uzaklik"] <= a1 * TETIK_YAKINLIK_ATR
    if derin and (kur["durum"].startswith("BOLGEDE") or yakin):
        try:
            tetik = KUR.footprint_tetik(kur["yon"])
        except Exception as ex:
            log(f"{ad}: footprint okunamadi ({type(ex).__name__}: {ex})")
    ortak["tetik"] = tetik

    skor, detay, _ = skorla(kur, yon, fiyat, eq, kirilim, tetik, derin)
    ortak["skor"] = skor
    ortak["detay"] = detay

    # ONAY icin skor TEK BASINA yetmez: alinabilir bir plan da olmali.
    p = kur["plan"]
    alinabilir = bool(p) and p["rr"] >= KUR.ASGARI_RR and \
        kur["durum"].startswith("BOLGEDE")
    if skor >= ESIK_ONAY and alinabilir:
        ortak["durum"] = "LONG_ONAY" if yon == "long" else "SHORT_ONAY"
    elif skor >= ESIK_HAZIRLIK:
        ortak["durum"] = "LONG_HAZIRLIK" if yon == "long" else "SHORT_HAZIRLIK"
    else:
        ortak["durum"] = "NOTR"
    return ortak


# ---------------------------------------------------------------- mesajlasma

EMOJI = {
    "NOTR": "⏸️",
    "LONG_YOK": "🚫",
    "LONG_HAZIRLIK": "🟡",
    "LONG_ONAY": "🟢",
    "SHORT_HAZIRLIK": "🟠",
    "SHORT_ONAY": "🔴",
}

BASLIK = {
    "NOTR": "KURULUM YOK",
    "LONG_YOK": "LONG ŞARTLARI YOK",
    "LONG_HAZIRLIK": "LONG HAZIRLIK",
    "LONG_ONAY": "LONG — İŞLEM HAZIR",
    "SHORT_HAZIRLIK": "SHORT HAZIRLIK",
    "SHORT_ONAY": "SHORT — İŞLEM HAZIR",
}


def fmt(x, ondalik=2):
    if x is None:
        return "-"
    return f"{x:,.{ondalik}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _ondalik(x, basamak=1):
    """Turkce ondalik: 3.5 -> '3,5'. Fiyatlarla ayni bicim olsun."""
    return f"{x:.{basamak}f}".replace(".", ",")


def _yuzde(hedef, giris):
    """Turkce yuzde: '+%4,34' / '-%1,24'."""
    if not giris:
        return "-"
    o = (hedef - giris) / giris * 100.0
    return f"{'+' if o >= 0 else '-'}%{_ondalik(abs(o), 2)}"


def _plan_blogu(r):
    """Giris / stop / hedef - mesajin en cok bakilan yeri, en ustte ve sade."""
    p = r["plan"]
    if not p:
        return []
    ok = "AL" if r["yon"] == "long" else "SAT"
    satirlar = [
        "<b>━━━━━ İŞLEM PLANI ━━━━━</b>",
        f"🟢 <b>GİRİŞ  {fmt(p['giris'])}</b>   ({ok})",
        f"🛑 <b>STOP   {fmt(p['stop'])}</b>   {_yuzde(p['stop'], p['giris'])}",
        f"🎯 <b>HEDEF  {fmt(p['hedef'])}</b>   {_yuzde(p['hedef'], p['giris'])}",
        "",
        f"⚖️ R:R <b>1 : {_ondalik(p['rr'])}</b>   ·   "
        f"riskin {_ondalik(p['rr'])} katı hedefleniyor",
        f"<i>Risk {fmt(p['risk'])} · Ödül {fmt(p['odul'])}</i>",
    ]
    if p.get("stop_genisletildi"):
        satirlar.append("<i>Stop, gürültü tabanına genişletildi — "
                        "yapının verdiği mesafe fazla dardı.</i>")
    return satirlar + [""]


def _neden_blogu(r):
    """Skorun nereden geldigi — her satir tek bir sartin cevabi."""
    d = r["detay"]
    if not d:
        return []
    p = r["plan"]
    z = r["bolge_fvg"]
    yon = r["yon"]

    def isaret(k):
        alinan, azami = d[k]
        return "✅" if alinan == azami else ("🟡" if alinan else "⛔")

    def puan(k):
        return f"({d[k][0]}/{d[k][1]})"

    out = ["<b>━━━━━ NEDEN ━━━━━</b>"]
    if "yon" in d:
        out.append(f"{isaret('yon')} Yön: {r['yon_sebep']} {puan('yon')}")
    if "bolge" in d:
        if z:
            nerede = ("fiyat İÇİNDE" if r["kur_durum"].startswith("BOLGEDE")
                      else "fiyat henüz gelmedi")
            out.append(f"{isaret('bolge')} Bölge: {z['periyot']} {z['tip']} FVG "
                       f"{fmt(z['alt'])}–{fmt(z['ust'])}, {nerede} {puan('bolge')}")
        else:
            out.append(f"⛔ Bölge: yönle uyumlu FVG yok {puan('bolge')}")
    if "rr" in d:
        out.append(f"{isaret('rr')} R:R "
                   + (_ondalik(p["rr"]) if p else "plan kurulamadı")
                   + f" {puan('rr')}")
    if "pd" in d:
        istenen = "DISCOUNT" if yon == "long" else "PREMIUM"
        out.append(f"{isaret('pd')} Konum: {r['bolge']} "
                   f"({yon} için istenen {istenen}) {puan('pd')}")
    if "kirilim" in d:
        k = {"bull": "yukarı", "bear": "aşağı"}.get(r["kirilim"], "yok")
        out.append(f"{isaret('kirilim')} 15dk yapı kırılımı: {k} {puan('kirilim')}")
    if "supurme" in d:
        sp = r["supurme"] or {"lehte": [], "aleyhte": []}
        if sp["lehte"]:
            k = sp["lehte"][0]
            ne = "dip" if k["tip"] == "ssl" else "tepe"
            out.append(f"{isaret('supurme')} Likidite: {k['periyot']} {ne} "
                       f"süpürüldü {fmt(k['seviye'])} {puan('supurme')}")
            ek = [f"{k['bar_once']} bar önce"]
            if k.get("esit_uc", 0) >= 2:
                ek.append(f"{k['esit_uc']} uç üst üste — gerçek stop havuzu")
            a = k.get("atr") or 0
            if a and k.get("sarkma", 0) >= a * 0.25:
                ek.append("derin sarkma")
            ek.append(f"geri alım {fmt(k['geri_alim'])}")
            out.append(f"      <i>{' · '.join(ek)}</i>")
        else:
            out.append(f"⛔ Likidite: lehte süpürme yok {puan('supurme')}")
        for k in sp["aleyhte"]:
            ne = "dip" if k["tip"] == "ssl" else "tepe"
            out.append(f"⚠️ Ters yönde {k['periyot']} {ne} süpürüldü "
                       f"{fmt(k['seviye'])} — o taraftaki yakıt bitti, "
                       f"süpürme puanı kırıldı")
    if "tetik" not in d and r.get("tetik"):
        out.append(f"➖ Footprint: ölçülemedi ({r['tetik'].get('sebep')}) — "
                   f"skordan çıkarıldı, sıfır sayılmadı")
    if "tetik" in d:
        t = r["tetik"]
        if t:
            if t["tetikler"]:
                en = t["tetikler"][-1]
                out.append(f"{isaret('tetik')} Footprint: {en['sebep']} "
                           f"({en['zaman'][-9:]}) {puan('tetik')}")
            else:
                out.append(f"{isaret('tetik')} Footprint: tetik yok, "
                           f"order flow dönmedi {puan('tetik')}")
            sb = t["son_bar"]
            out.append(f"      <i>CVD {t['cvd_yon']} · son bar delta "
                       f"{sb['delta_orani']:+.0%} · dengesizlik "
                       f"A{sb['dengesizlik_alis']}/S{sb['dengesizlik_satis']}</i>")
        else:
            out.append(f"⛔ Footprint: fiyat bölgeden uzak, bakılmadı "
                       f"{puan('tetik')}")
    return out + [""]


def mesaj_olustur(r, eski_durum, eski_skor):
    e = EMOJI.get(r["durum"], "•")
    b = BASLIK.get(r["durum"], r["durum"])
    eski_b = BASLIK.get(eski_durum, eski_durum)

    satirlar = [
        f"{e} <b>{r['ad']} — {b}</b>",
        f"Fiyat <b>{fmt(r['fiyat'])}</b>   ·   Skor <b>{r['skor']}</b>/100",
        f"<i>Önceki: {eski_b} ({eski_skor})</i>",
        "",
    ]

    if r["durum"] == "LONG_YOK":
        satirlar += [
            f"Yapı aşağı: {r['yon_sebep']}",
            "",
            "BIST'te açığa satış yok — bu bir <b>short sinyali değildir</b>.",
            "Yeni long açma; pozisyondaysan yapıyı gözden geçir.",
            "",
        ]
    elif r["durum"] == "NOTR":
        satirlar += [f"Sebep: <b>{r['kur_durum']}</b>",
                     f"Yön: {r['yon_sebep']}", ""]
        if r["plan"]:
            satirlar += ["<i>Taslak plan (şartlar tamam değil):</i>"]
            satirlar += _plan_blogu(r)
        satirlar += _neden_blogu(r)
    else:
        satirlar += _plan_blogu(r)
        if r["durum"].endswith("HAZIRLIK"):
            eksik = [k for k, (a, m) in r["detay"].items() if a < m]
            satirlar += [
                "⏳ <b>Henüz giriş değil.</b> Şartların hepsi tamam değil"
                + (f" (eksik: {', '.join(eksik)})." if eksik else "."),
                "Seviyeler yukarıda — onay gelirse aynı plan geçerli.",
                "",
            ]
        satirlar += _neden_blogu(r)

    # Vekil kaynak kullanildiysa acikca soyle - yanlis kesinlik satma
    if r.get("vekil"):
        o = r.get("oran")
        satirlar += ["🔄 <b>VEKİL KAYNAK</b> — 15dk yapı "
                     f"<b>{r['kaynak']}</b> üzerinden okundu."]
        if o:
            satirlar.append(f"NQ karşılığı için ×{o:.2f} ile çarp.")
        satirlar.append("")

    satirlar += [
        f"<i>Kaynak: {r.get('kaynak', '-')} · seviyeler "
        f"{KUR.SON_HTF_KAYNAK.get(r['ad'], '-')} · {r['zaman_utc']}</i>",
        "<i>Bu bir al/sat emri değildir — şartların durumudur.</i>",
    ]
    return "\n".join(satirlar)


def telegram_ayar():
    """Once ortam degiskeni (GitHub Secrets), yoksa yerel config dosyasi."""
    tok = os.environ.get("TELEGRAM_BOT_TOKEN")
    cid = os.environ.get("TELEGRAM_CHAT_ID")
    if tok and cid:
        return {"bot_token": tok, "chat_id": cid}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def telegram_gonder(metin):
    cfg = telegram_ayar()
    url = f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage"
    veri = urllib.parse.urlencode(
        {
            "chat_id": cfg["chat_id"],
            "text": metin,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    req = urllib.request.Request(url, data=veri, headers=UA)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r).get("ok", False)


# ---------------------------------------------------------------- ana akis


def state_yukle():
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def state_kaydet(s):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)


BIST_HISSELERI = ["ASELS", "TUPRS", "BIMAS"]


def bist_acik_mi():
    """BIST seans kontrolu: hafta ici 10:00 - 18:30 (yerel saat = TSI)."""
    simdi = datetime.now()
    if simdi.weekday() > 4:
        return False
    dk = simdi.hour * 60 + simdi.minute
    return 10 * 60 <= dk <= 18 * 60 + 30


def bar_yasi_dk(bars):
    """Son barin kac dakika onceye ait oldugu. Bayat veri korumasi."""
    son = bars[-1]["t"]
    return (datetime.now(timezone.utc).timestamp() - son) / 60.0


def _ayin_n_inci_pazari(yil, ay, n):
    ilk = 1 + (6 - datetime(yil, ay, 1).weekday()) % 7
    return ilk + (n - 1) * 7


def et_saati(u):
    """UTC -> New York saati. Yaz saatini EL ILE hesaplar; zoneinfo/tzdata
    kurulu olmayan makinelerde de calissin diye.

    ABD kurali (2007 sonrasi): Mart'in 2. Pazari 02:00 yerelden Kasim'in
    1. Pazari 02:00 yerele kadar EDT (UTC-4), disinda EST (UTC-5).
    Ofseti UTC-4'e sabitlemek kasim-mart arasi seansi bir saat kaydirir.
    """
    y = u.year
    basla = datetime(y, 3, _ayin_n_inci_pazari(y, 3, 2), 7, tzinfo=timezone.utc)
    bitir = datetime(y, 11, _ayin_n_inci_pazari(y, 11, 1), 6, tzinfo=timezone.utc)
    ofs = -4 if basla <= u < bitir else -5
    return u.astimezone(timezone(timedelta(hours=ofs)))


def nq_acik_mi():
    """CME e-mini: Pazar 18:00 ET - Cuma 17:00 ET, gunluk 17:00-18:00 ET bakim."""
    et = et_saati(datetime.now(timezone.utc))
    gun, saat = et.weekday(), et.hour     # Pazartesi=0 ... Pazar=6
    if gun == 5:                          # Cumartesi
        return False
    if gun == 6 and saat < 18:            # Pazar 18:00 oncesi
        return False
    if gun == 4 and saat >= 17:           # Cuma 17:00 sonrasi
        return False
    return saat != 17                     # gunluk bakim saati


def piyasa_acik_mi(ad):
    if ad == "BTCUSDT":
        return True
    if ad == "NQ1!":
        return nq_acik_mi()
    return bist_acik_mi()


def oran_guncelle(state):
    """NQ/QQQ oranini 12 saatte bir tazele. Vekil moda dusunce seviye cevirisinde
    kullanilir. Ikisi de taze degilse eskisini korur."""
    kayit = state.get("_oran", {})
    yas_saat = 99.0
    if kayit.get("zaman"):
        yas_saat = (datetime.now(timezone.utc).timestamp() - kayit["zaman"]) / 3600.0
    if kayit.get("NQ_QQQ") and yas_saat < 12:
        return kayit["NQ_QQQ"]
    try:
        nq = yahoo_15m("NQ%3DF")
        qq = yahoo_15m("QQQ")
        if yas_dk(nq) < 60 and yas_dk(qq) < 60:
            o = nq[-1]["c"] / qq[-1]["c"]
            state["_oran"] = {"NQ_QQQ": round(o, 4),
                              "zaman": datetime.now(timezone.utc).timestamp()}
            log(f"NQ/QQQ orani guncellendi: {o:.4f}")
            return o
    except Exception as ex:
        log(f"oran guncellenemedi: {ex}")
    return kayit.get("NQ_QQQ")


def baglanti_testi():
    """Telegram ayarlarini dogrular ve test mesaji atar. Sorun varsa net soyler."""
    tok = os.environ.get("TELEGRAM_BOT_TOKEN")
    cid = os.environ.get("TELEGRAM_CHAT_ID")
    log(f"TEST: TELEGRAM_BOT_TOKEN {'VAR' if tok else 'YOK'} "
        f"(uzunluk {len(tok) if tok else 0})")
    log(f"TEST: TELEGRAM_CHAT_ID   {'VAR' if cid else 'YOK'} "
        f"(deger {cid if cid else '-'})")
    if not tok or not cid:
        log("TEST BASARISIZ: GitHub Secrets eksik. "
            "Settings > Secrets and variables > Actions altina ekle.")
        return 1
    try:
        ok = telegram_gonder(
            "🔧 <b>BAĞLANTI TESTİ</b>\n\n"
            "GitHub Actions → Telegram bağlantısı <b>çalışıyor.</b>\n\n"
            "Bundan sonra SMC durumu değiştiğinde otomatik mesaj gelecek.\n"
            "<i>Bu mesaj elle tetiklendi.</i>")
        log(f"TEST: mesaj gonderildi -> {'BASARILI' if ok else 'BASARISIZ'}")
        return 0 if ok else 1
    except Exception as ex:
        log(f"TEST BASARISIZ: {type(ex).__name__}: {ex}")
        return 1


def main():
    if os.environ.get("SMC_TEST_MESAJI", "").lower() == "true":
        return baglanti_testi()

    state = state_yukle()

    izlenecek = [("BTCUSDT", 60), ("NQ1!", 60)]
    if bist_acik_mi():
        izlenecek += [(k, 45) for k in BIST_HISSELERI]
    else:
        log("BIST kapali (seans disi) - hisseler atlandi.")

    for ad, azami_yas in izlenecek:
        onceki_kayit = state.get(ad, {})
        b15, kaynak, vekil, yas, denemeler = bar_cek(ad, azami_yas, log)

        # --- hicbir kaynak taze veri veremedi
        if b15 is None or len(b15) < 60:
            if piyasa_acik_mi(ad):
                if not onceki_kayit.get("veri_sorunu"):
                    try:
                        telegram_gonder(
                            f"⚠️ <b>VERİ KAYNAĞI SORUNU — {ad}</b>\n\n"
                            f"Piyasa açık ama <b>hiçbir kaynak</b> taze veri vermedi.\n\n"
                            f"<b>Denenen kaynaklar:</b>\n"
                            + "\n".join(f"• {d}" for d in denemeler) +
                            f"\n\nKabul sınırı: {azami_yas} dk.\n\n"
                            f"<b>{ad} takibi DURDU.</b> Bu enstrümanda mesaj gelmemesi "
                            f"\"kurulum yok\" anlamına gelmez — sistem kör.\n\n"
                            f"<i>Veri döndüğünde haber vereceğim.</i>"
                        )
                        log(f"{ad}: VERI SORUNU bildirimi gonderildi")
                    except Exception as ex:
                        log(f"{ad}: veri sorunu bildirimi gonderilemedi: {ex}")
                onceki_kayit["veri_sorunu"] = True
                onceki_kayit["son_yas_dk"] = round(yas) if yas < 9e8 else None
                onceki_kayit["denemeler"] = denemeler
                state[ad] = onceki_kayit
            else:
                log(f"{ad}: veri bayat - piyasa kapali, normal. ({', '.join(denemeler)})")
            continue

        # --- vekil kaynak kullanildiysa oran hazirla
        oran = oran_guncelle(state) if vekil else None

        # Veri tazeyse ve daha once sorun bildirildiyse, duzeldigini haber ver
        if onceki_kayit.get("veri_sorunu"):
            try:
                telegram_gonder(f"✅ <b>{ad} VERİSİ GERİ GELDİ</b>\n\n"
                                f"Takip normale döndü.\n"
                                f"Kaynak: <b>{kaynak}</b> · son bar {yas:.0f} dk önce.")
                log(f"{ad}: veri duzeldi bildirimi gonderildi")
            except Exception as ex:
                log(f"{ad}: duzelme bildirimi gonderilemedi: {ex}")

        # --- ust periyot verisi (yon + bolge + plan burada kurulur)
        try:
            htf = KUR.veri_cek(ad, log=log)
        except Exception as ex:
            log(f"{ad}: UST PERIYOT VERISI ALINAMADI ({type(ex).__name__}: {ex})")
            if piyasa_acik_mi(ad) and not onceki_kayit.get("htf_sorunu"):
                try:
                    telegram_gonder(
                        f"⚠️ <b>{ad} — ÜST PERİYOT VERİSİ YOK</b>\n\n"
                        f"15dk verisi geldi ama haftalık/günlük/4H veri "
                        f"alınamadı ({type(ex).__name__}).\n\n"
                        f"Plan kurulamıyor. <b>{ad} bu turda değerlendirilmedi</b> — "
                        f"mesaj gelmemesi \"kurulum yok\" demek değil.")
                except Exception as ex2:
                    log(f"{ad}: htf sorunu bildirimi gonderilemedi: {ex2}")
            onceki_kayit["htf_sorunu"] = True
            state[ad] = onceki_kayit
            continue

        r = degerlendir(ad, b15, htf)
        r["kaynak"] = kaynak
        r["vekil"] = vekil
        r["oran"] = oran
        eski_durum = onceki_kayit.get("durum", "BASLANGIC")
        eski_skor = onceki_kayit.get("skor", 0)

        if r["durum"] != eski_durum:
            try:
                ok = telegram_gonder(mesaj_olustur(r, eski_durum, eski_skor))
                log(f"{ad}: {eski_durum} -> {r['durum']} (skor {r['skor']}) TELEGRAM={'OK' if ok else 'HATA'}")
            except Exception as ex:
                log(f"{ad}: Telegram gonderim hatasi: {ex}")
        else:
            log(f"{ad}: {r['durum']} degismedi (skor {eski_skor}->{r['skor']}, fiyat {r['fiyat']:.2f}) - sessiz")

        p = r["plan"]
        state[ad] = {
            "veri_sorunu": False,
            "htf_sorunu": False,
            "kaynak": kaynak,
            "htf_kaynak": KUR.SON_HTF_KAYNAK.get(ad, "-"),
            "vekil": vekil,
            "veri_yas_dk": round(yas),
            "durum": r["durum"],
            "skor": r["skor"],
            "yon": r["yon"],
            "fiyat": r["fiyat"],
            "bias": r["yon_smc"],
            "kurulum": r["kur_durum"],
            "plan": ({"giris": p["giris"], "stop": p["stop"],
                      "hedef": p["hedef"], "rr": round(p["rr"], 2)}
                     if p else None),
            "bolge": r["bolge"],
            "eq": r["eq"],
            "aralik": [r["aralik_dip"], r["aralik_tepe"]],
            "zaman_utc": r["zaman_utc"],
            "guncelleme": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        }

    state_kaydet(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
