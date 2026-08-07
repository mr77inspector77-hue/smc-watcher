#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SMC Watcher - 5 enstruman icin otomatik Smart Money Concepts durum takibi.
(NQ1!, BTCUSDT, ASELS, TUPRS, BIMAS)

15 dakikada bir calisir (cron veya Windows zamanlanmis gorevi).
Claude'a veya TradingView'a bagimli DEGILDIR.

Veri: veri_kaynaklari.py uzerinden COKLU KAYNAK, otomatik yedege gecisli.
Durum degistiginde Telegram'a mesaj atar. Degismezse sessiz kalir.
Hicbir kaynak veri veremezse "kor kaldim" uyarisi gonderir.
"""

import json
import os
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from veri_kaynaklari import bar_cek, yahoo_15m, yas_dk  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "telegram_config.json")
STATE_PATH = os.path.join(BASE, "smc_state_auto.json")
LOG_PATH = os.path.join(BASE, "smc_watch.log")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# Skor esikleri
ESIK_ONAY = 70
ESIK_HAZIRLIK = 45

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


def _bolgede_mi(fiyat, fvgler, tip, tolerans_yuzde=0.15):
    """Fiyat uygun tipte bir FVG icinde mi (veya cok yakininda mi)."""
    for g in fvgler:
        if g["tip"] != tip:
            continue
        if g["alt"] <= fiyat <= g["ust"]:
            return 20, g
        tol = fiyat * tolerans_yuzde / 100.0
        if (g["alt"] - tol) <= fiyat <= (g["ust"] + tol):
            return 10, g
    return 0, None


def skorla(yon, bias, fiyat, eq, fvgler, sweeps, kirilim):
    """yon: 'long' | 'short' -> 0-100 skor + kirilim detayi"""
    d = {}
    # 1) HTF bias uyumu (25)
    if bias == "RANGE":
        d["bias"] = 12
    elif (yon == "long" and bias == "BULLISH") or (yon == "short" and bias == "BEARISH"):
        d["bias"] = 25
    else:
        d["bias"] = 0
    # 2) Premium/Discount dogru taraf (20)
    if yon == "long":
        d["pd"] = 20 if fiyat < eq else 0
    else:
        d["pd"] = 20 if fiyat > eq else 0
    # 3) OB/FVG bolgesi (20)
    puan, gap = _bolgede_mi(fiyat, fvgler, "bull" if yon == "long" else "bear")
    d["bolge"] = puan
    # 4) Likidite supurmesi (20)
    if yon == "long":
        d["supurme"] = 20 if sweeps["ssl"] else 0
    else:
        d["supurme"] = 20 if sweeps["bsl"] else 0
    # 5) 15dk yapi kirilimi teyidi (15)
    if (yon == "long" and kirilim == "bull") or (yon == "short" and kirilim == "bear"):
        d["kirilim"] = 15
    else:
        d["kirilim"] = 0
    return sum(d.values()), d, gap


def degerlendir(ad, bars15, bars1h):
    fiyat = bars15[-1]["c"]
    bias = htf_bias(bars1h)
    hi, lo, eq = dealing_range(bars15)
    fvgler = find_fvgs(bars15)
    sweeps = find_sweeps(bars15)
    kirilim, son_tepe, son_dip = structure_break(bars15)

    long_skor, long_d, long_gap = skorla("long", bias, fiyat, eq, fvgler, sweeps, kirilim)
    short_skor, short_d, short_gap = skorla("short", bias, fiyat, eq, fvgler, sweeps, kirilim)

    if long_skor >= short_skor:
        yon, skor, detay, gap = "long", long_skor, long_d, long_gap
    else:
        yon, skor, detay, gap = "short", short_skor, short_d, short_gap

    if skor >= ESIK_ONAY:
        durum = "LONG_ONAY" if yon == "long" else "SHORT_ONAY"
    elif skor >= ESIK_HAZIRLIK:
        durum = "LONG_HAZIRLIK" if yon == "long" else "SHORT_HAZIRLIK"
    else:
        durum = "NOTR"

    return {
        "ad": ad,
        "durum": durum,
        "skor": skor,
        "yon": yon,
        "fiyat": fiyat,
        "bias": bias,
        "aralik_tepe": hi,
        "aralik_dip": lo,
        "eq": eq,
        "bolge": "PREMIUM" if fiyat > eq else "DISCOUNT",
        "detay": detay,
        "gap": gap,
        "sweeps": sweeps,
        "kirilim": kirilim,
        "son_tepe": son_tepe,
        "son_dip": son_dip,
        "zaman_utc": datetime.fromtimestamp(bars15[-1]["t"], timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        ),
    }


# ---------------------------------------------------------------- mesajlasma

EMOJI = {
    "NOTR": "⏸️",
    "LONG_HAZIRLIK": "🟢",
    "LONG_ONAY": "🔵",
    "SHORT_HAZIRLIK": "🟠",
    "SHORT_ONAY": "🔴",
}

BASLIK = {
    "NOTR": "NÖTR",
    "LONG_HAZIRLIK": "LONG HAZIRLIK",
    "LONG_ONAY": "LONG ŞARTLARI TAMAM",
    "SHORT_HAZIRLIK": "SHORT HAZIRLIK",
    "SHORT_ONAY": "SHORT ŞARTLARI TAMAM",
}


def fmt(x, ondalik=2):
    if x is None:
        return "-"
    return f"{x:,.{ondalik}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def mesaj_olustur(r, eski_durum, eski_skor):
    e = EMOJI.get(r["durum"], "•")
    b = BASLIK.get(r["durum"], r["durum"])
    d = r["detay"]

    # BIST tek yonlu: short durumu bir "sat" sinyali degil, "long sartlari yok" demek
    tek_yonlu = r["ad"] in BIST_HISSELERI
    uyari = None
    if tek_yonlu and r["durum"].startswith("SHORT"):
        b = "LONG ŞARTLARI YOK"
        e = "🚫"
        uyari = ("Satıcı baskın. BIST'te açığa satış yok — bu bir short sinyali değil. "
                 "Yeni long açma; pozisyondaysan yapıyı gözden geçir.")

    satirlar = [
        f"<b>{e} {r['ad']} — {b}</b>",
        "",
        f"Fiyat: <b>{fmt(r['fiyat'])}</b>",
        f"Skor: <b>{r['skor']}</b>/100   (önceki: {eski_skor})",
        f"Önceki durum: {BASLIK.get(eski_durum, eski_durum)}",
        "",
        f"HTF bias: <b>{r['bias']}</b>",
        f"Bölge: <b>{r['bolge']}</b>  (EQ {fmt(r['eq'])})",
        f"24s aralık: {fmt(r['aralik_dip'])} — {fmt(r['aralik_tepe'])}",
        "",
        "<b>Skor kırılımı</b>",
        f"• HTF bias uyumu: {d['bias']}/25",
        f"• Premium/Discount: {d['pd']}/20",
        f"• OB-FVG bölgesi: {d['bolge']}/20",
        f"• Likidite süpürmesi: {d['supurme']}/20",
        f"• 15dk yapı kırılımı: {d['kirilim']}/15",
    ]

    if r["gap"]:
        g = r["gap"]
        satirlar += ["", f"FVG: {fmt(g['alt'])} — {fmt(g['ust'])} ({g['tip']})"]

    s = r["sweeps"]
    if s["ssl"]:
        satirlar.append(f"SSL süpürüldü: {fmt(s['ssl_seviye'])}")
    if s["bsl"]:
        satirlar.append(f"BSL süpürüldü: {fmt(s['bsl_seviye'])}")

    satirlar += [
        "",
        f"Yakın yapı: dip {fmt(r['son_dip'])} / tepe {fmt(r['son_tepe'])}",
    ]
    if uyari:
        satirlar += ["", f"⚠️ {uyari}"]

    # Vekil kaynak kullanildiysa acikca soyle - yanlis kesinlik satma
    if r.get("vekil"):
        o = r.get("oran")
        satirlar += ["", "🔄 <b>VEKİL KAYNAK KULLANILDI</b>",
                     f"Ana kaynak veri vermedi, yapı <b>{r['kaynak']}</b> üzerinden okundu.",
                     "Yön ve yapı geçerli, ancak <b>seviyeler yaklaşıktır</b>."]
        if o:
            satirlar.append(f"NQ karşılığı için ×{o:.2f} ile çarp "
                            f"(≈ {fmt(r['fiyat'] * o)}).")
    else:
        satirlar += ["", f"<i>Kaynak: {r.get('kaynak', '-')}</i>"]

    satirlar += [
        f"<i>{r['zaman_utc']}</i>",
        "<i>Bu bir al/sat emri değildir — SMC şartlarının durumudur.</i>",
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


def nq_acik_mi():
    """CME e-mini: Pazar 18:00 ET - Cuma 17:00 ET, gunluk 17:00-18:00 ET bakim.
    ET = UTC-4 (yaz saati)."""
    u = datetime.now(timezone.utc)
    et_saat = (u.hour - 4) % 24
    et_gun = u.weekday() if u.hour >= 4 else (u.weekday() - 1) % 7
    if et_gun == 5:                      # Cumartesi
        return False
    if et_gun == 6 and et_saat < 18:     # Pazar 18:00 oncesi
        return False
    if et_gun == 4 and et_saat >= 17:    # Cuma 17:00 sonrasi
        return False
    return et_saat != 17                 # gunluk bakim saati


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

        r = degerlendir(ad, b15, resample_to_1h(b15))
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

        state[ad] = {
            "veri_sorunu": False,
            "kaynak": kaynak,
            "vekil": vekil,
            "veri_yas_dk": round(yas),
            "durum": r["durum"],
            "skor": r["skor"],
            "yon": r["yon"],
            "fiyat": r["fiyat"],
            "bias": r["bias"],
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
