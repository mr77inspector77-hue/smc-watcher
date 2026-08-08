#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BIST SMC izleyicisi — otomatik tarama + Telegram sinyali.

Kapsam:
    Enstruman   yalniz BIST hisseleri (TAKIP_LISTESI)
    Periyot     Gunluk / 4H / 1H
    Yon         YALNIZ LONG
    Yontem      SMC — yapi yonu, FVG bolgesi, likidite seviyeleri,
                likidite supurmesi, premium/discount konumu

Yapiyi kurulum.py motoru kurar, skoru ve Telegram mesajini bu dosya yapar.
Ayni motor elle `python kurulum.py ASELS` ile de calisir; kural tektir.

Skor 0-100. ONAY icin skor TEK BASINA yetmez: somut bir plan
(giris/stop/hedef), R:R >= esik ve fiyatin bolgeye VARMIS olmasi sarttir.
Skor "ne kadar guzel", plan "alinabilir mi" sorusunu yanitlar.

BIST seansinda calisir (hafta ici 10:00-18:30 TSI). Durum degistiginde
Telegram'a mesaj atar, degismezse sessiz kalir. Veri gelmezse "kor kaldim"
uyarisi gonderir - mesaj gelmemesi "kurulum yok" anlamina gelmesin.
"""

import json
import os
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kurulum as KUR  # noqa: E402   ortak analiz motoru

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "telegram_config.json")
STATE_PATH = os.path.join(BASE, "smc_state_auto.json")
LOG_PATH = os.path.join(BASE, "smc_watch.log")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# ---------------------------------------------------------------------
# TAKIP LISTESI — izlenecek hisseler. Degistirmek icin burayi duzenle.
#
# BIST30 olcegi. XU030'un RESMI bilesimi periyodik revize edilir ve bu
# koddan dogrulanamaz; liste, dogrulanmis 43 aday arasindan ORTALAMA
# GUNLUK ISLEM HACMI en yuksek 30 kod alinarak kuruldu (2026-08-08).
# Her kodun Yahoo'dan veri dondugu tek tek test edildi.
# KOZAL ve KOZAA disarida: Yahoo 404 doner, veri yok.
# ---------------------------------------------------------------------
TAKIP_LISTESI = [
    "THYAO", "ASELS", "AKBNK", "ASTOR", "YKBNK", "TUPRS",
    "ISCTR", "EREGL", "KCHOL", "BIMAS", "GARAN", "SAHOL",
    "TCELL", "EKGYO", "SASA", "KRDMD", "TTKOM", "PETKM",
    "SISE", "FROTO", "PGSUS", "HALKB", "MGROS", "HEKTS",
    "TOASO", "VAKBN", "GUBRF", "TAVHL", "ENKAI", "BRSAN",
]

# Hisseler arasi bekleme. 30 hisse = 60 Yahoo istegi; arka arkaya
# atinca saglayici bogazliyor (429). Kucuk bir ara turu kurtariyor.
ISTEK_ARASI_SN = 0.4

# Skor esikleri
ESIK_ONAY = 70
ESIK_HAZIRLIK = 45

# Katman agirliklari (toplam 100).
AGIRLIK = {
    "yon": 30,        # gunluk yapi + 4H teyidi
    "bolge": 25,      # yonle uyumlu FVG ve fiyatin ona varmis olmasi
    "rr": 20,         # planin risk/odul kalitesi
    "supurme": 15,    # likidite supurmesi (dereceli)
    "pd": 10,         # discount tarafta olmak
}

# Aleyhte supurme cezasi: long bakarken tepe supuruldiyse yukaridaki yakit
# zaten harcanmis demektir. Lehteki supurmeyi silmez, degerini kirar.
ALEYHTE_CARPAN = 0.55

# 1H barin bu yastan eskiyse veri bayat sayilir (seans icinde).
AZAMI_YAS_DK = 90


# ---------------------------------------------------------------- yardimci


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def bar_yasi_dk(bars):
    if not bars:
        return 9e9
    return (datetime.now(timezone.utc).timestamp() - bars[-1]["t"]) / 60.0


def bist_acik_mi():
    """BIST seans kontrolu: hafta ici 10:00 - 18:30 (yerel saat = TSI)."""
    simdi = datetime.now()
    if simdi.weekday() > 4:
        return False
    dk = simdi.hour * 60 + simdi.minute
    return 10 * 60 <= dk <= 18 * 60 + 30


# ---------------------------------------------------------------- skor


def dealing_range(bars, bar_sayisi=40):
    son = bars[-bar_sayisi:]
    hi = max(b["h"] for b in son)
    lo = min(b["l"] for b in son)
    return hi, lo, (hi + lo) / 2.0


def skorla(kur, fiyat, eq):
    """kurulum.py ciktisini 0-100 skora cevirir.

    Doner: (skor, detay) - detay her katman icin (alinan, azami).
    """
    d = {}

    # 1) YON — gunluk yapi, 4H teyidi derecelendirir.
    #    notr = 4H kararsiz (itiraz degil) · zayif = 4H TERS (gercek itiraz)
    guc = kur.get("yon_guc")
    d["yon"] = AGIRLIK["yon"] * {"uyumlu": 1.0, "notr": 0.75,
                                 "zayif": 0.4}.get(guc, 0.0)

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

    # 4) SUPURME — dereceli; aleyhte supurme varsa deger kirilir
    sp = kur.get("supurme") or {"lehte": [], "aleyhte": []}
    if sp["lehte"]:
        g = sp["lehte"][0]["guc"]
        if sp["aleyhte"]:
            g *= ALEYHTE_CARPAN
        d["supurme"] = AGIRLIK["supurme"] * g
    else:
        d["supurme"] = 0

    # 5) Discount tarafta olmak (long icin ucuz taraf)
    d["pd"] = AGIRLIK["pd"] if (eq is not None and fiyat < eq) else 0

    skor = round(sum(d.values()))
    return skor, {k: (round(v), AGIRLIK[k]) for k, v in d.items()}


def degerlendir(ad, veri):
    """Yapiyi kurulum.py kurar, skoru burasi olcer."""
    kur = KUR.kurulum_kur(ad, veri)
    fiyat = kur["fiyat"]

    gunluk = veri.get("1d") or []
    if len(gunluk) >= 40:
        hi, lo, eq = dealing_range(gunluk, 40)
    else:
        hi = lo = eq = None

    r = {
        "ad": ad, "fiyat": fiyat, "yon_smc": kur["yon"],
        "yon_sebep": kur["yon_sebep"], "kur_durum": kur["durum"],
        "bolge_fvg": kur["bolge"], "plan": kur["plan"],
        "supurme": kur["supurme"],
        "aralik_tepe": hi, "aralik_dip": lo, "eq": eq,
        "bolge": ("-" if eq is None else ("DISCOUNT" if fiyat < eq else "PREMIUM")),
        "detay": {}, "skor": 0,
        "zaman_utc": datetime.fromtimestamp(veri["1h"][-1]["t"], timezone.utc)
                             .strftime("%Y-%m-%d %H:%M UTC"),
    }

    if kur["durum"].startswith("SHORT YON"):
        r["durum"] = "LONG_YOK"
        return r
    if kur["yon"] == "RANGE":
        r["durum"] = "NOTR"
        return r

    skor, detay = skorla(kur, fiyat, eq)
    r["skor"], r["detay"] = skor, detay

    p = kur["plan"]
    alinabilir = bool(p) and p["rr"] >= KUR.ASGARI_RR and \
        kur["durum"].startswith("BOLGEDE")
    if skor >= ESIK_ONAY and alinabilir:
        r["durum"] = "LONG_SINYAL"
    elif skor >= ESIK_HAZIRLIK:
        r["durum"] = "LONG_HAZIRLIK"
    else:
        r["durum"] = "NOTR"
    return r


# ---------------------------------------------------------------- mesaj

EMOJI = {"NOTR": "⏸️", "LONG_YOK": "🚫",
         "LONG_HAZIRLIK": "🟡", "LONG_SINYAL": "🟢"}

BASLIK = {"NOTR": "KURULUM YOK", "LONG_YOK": "LONG ŞARTLARI YOK",
          "LONG_HAZIRLIK": "LONG HAZIRLIK", "LONG_SINYAL": "LONG SİNYAL"}


def fmt(x, ondalik=2):
    if x is None:
        return "-"
    return f"{x:,.{ondalik}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _ondalik(x, basamak=1):
    return f"{x:.{basamak}f}".replace(".", ",")


def _yuzde(hedef, giris):
    if not giris:
        return "-"
    o = (hedef - giris) / giris * 100.0
    return f"{'+' if o >= 0 else '-'}%{_ondalik(abs(o), 2)}"


def _plan_blogu(r):
    """Giris / stop / hedef — mesajin en cok bakilan yeri, en ustte."""
    p = r["plan"]
    if not p:
        return []
    satirlar = [
        "<b>━━━━━ İŞLEM PLANI ━━━━━</b>",
        f"🟢 <b>GİRİŞ  {fmt(p['giris'])}</b>",
        f"🛑 <b>STOP   {fmt(p['stop'])}</b>   {_yuzde(p['stop'], p['giris'])}",
        f"🎯 <b>HEDEF  {fmt(p['hedef'])}</b>   {_yuzde(p['hedef'], p['giris'])}",
        "",
        f"⚖️ R:R <b>1 : {_ondalik(p['rr'])}</b>",
    ]
    if p.get("stop_genisletildi"):
        satirlar.append("<i>Stop gürültü tabanına genişletildi — yapının "
                        "verdiği mesafe fazla dardı.</i>")
    return satirlar + [""]


def _neden_blogu(r):
    """Skorun nereden geldigi — her satir tek bir sartin cevabi."""
    d = r["detay"]
    if not d:
        return []
    p, z = r["plan"], r["bolge_fvg"]

    def isaret(k):
        alinan, azami = d[k]
        return "✅" if alinan == azami else ("🟡" if alinan else "⛔")

    def puan(k):
        return f"({d[k][0]}/{d[k][1]})"

    out = ["<b>━━━━━ NEDEN ━━━━━</b>",
           f"{isaret('yon')} Yön: {r['yon_sebep']} {puan('yon')}"]
    if z:
        nerede = ("fiyat İÇİNDE" if r["kur_durum"].startswith("BOLGEDE")
                  else "fiyat henüz gelmedi")
        out.append(f"{isaret('bolge')} Bölge: {z['periyot']} FVG "
                   f"{fmt(z['alt'])}–{fmt(z['ust'])}, {nerede} {puan('bolge')}")
    else:
        out.append(f"⛔ Bölge: yönle uyumlu FVG yok {puan('bolge')}")
    out.append(f"{isaret('rr')} R:R "
               + (_ondalik(p["rr"]) if p else "plan kurulamadı")
               + f" {puan('rr')}")

    sp = r["supurme"] or {"lehte": [], "aleyhte": []}
    if sp["lehte"]:
        k = sp["lehte"][0]
        out.append(f"{isaret('supurme')} Likidite: {k['periyot']} dip "
                   f"süpürüldü {fmt(k['seviye'])} {puan('supurme')}")
        ek = [f"{k['bar_once']} bar önce"]
        if k.get("esit_uc", 0) >= 2:
            ek.append(f"{k['esit_uc']} uç üst üste — gerçek stop havuzu")
        a = k.get("atr") or 0
        if a and k.get("sarkma", 0) >= a * 0.25:
            ek.append("derin sarkma")
        out.append(f"      <i>{' · '.join(ek)}</i>")
    else:
        out.append(f"⛔ Likidite: lehte süpürme yok {puan('supurme')}")
    for k in sp["aleyhte"]:
        out.append(f"⚠️ Ters yönde {k['periyot']} tepe süpürüldü "
                   f"{fmt(k['seviye'])} — yukarıdaki yakıt bitti, "
                   f"süpürme puanı kırıldı")

    out.append(f"{isaret('pd')} Konum: {r['bolge']} "
               f"(long için istenen DISCOUNT) {puan('pd')}")
    return out + [""]


def mesaj_olustur(r, eski_durum, eski_skor):
    e = EMOJI.get(r["durum"], "•")
    b = BASLIK.get(r["durum"], r["durum"])
    satirlar = [
        f"{e} <b>{r['ad']} — {b}</b>",
        f"Fiyat <b>{fmt(r['fiyat'])}</b>   ·   Skor <b>{r['skor']}</b>/100",
        f"<i>Önceki: {BASLIK.get(eski_durum, eski_durum)} ({eski_skor})</i>",
        "",
    ]

    if r["durum"] == "LONG_YOK":
        satirlar += [f"Yapı aşağı: {r['yon_sebep']}", "",
                     "BIST'te açığa satış yok — bu bir <b>short sinyali "
                     "değildir</b>.", "Yeni long açma; pozisyondaysan yapıyı "
                     "gözden geçir.", ""]
    elif r["durum"] == "NOTR":
        satirlar += [f"Sebep: <b>{r['kur_durum']}</b>",
                     f"Yön: {r['yon_sebep']}", ""]
        satirlar += _neden_blogu(r)
    else:
        satirlar += _plan_blogu(r)
        if r["durum"] == "LONG_HAZIRLIK":
            eksik = [k for k, (a, m) in r["detay"].items() if a < m]
            satirlar += ["⏳ <b>Henüz giriş değil.</b> Şartların hepsi tamam "
                         "değil" + (f" (eksik: {', '.join(eksik)})." if eksik
                                    else "."),
                         "Seviyeler yukarıda — sinyal gelirse aynı plan geçerli.",
                         ""]
        satirlar += _neden_blogu(r)

    satirlar += [f"<i>{r['zaman_utc']} · Yahoo</i>",
                 "<i>Bu bir al/sat emri değildir — şartların durumudur.</i>"]
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
    veri = urllib.parse.urlencode({
        "chat_id": cfg["chat_id"], "text": metin,
        "parse_mode": "HTML", "disable_web_page_preview": "true",
    }).encode("utf-8")
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


def baglanti_testi():
    try:
        ok = telegram_gonder(
            "🔧 <b>BIST SMC izleyici — bağlantı testi</b>\n\n"
            f"Takip listesi: <b>{', '.join(TAKIP_LISTESI)}</b>\n"
            "Periyotlar: Günlük / 4H / 1H · yalnız LONG\n\n"
            "<i>Bu bir sinyal değildir.</i>")
        log(f"TEST MESAJI: {'OK' if ok else 'HATA'}")
        return 0 if ok else 1
    except Exception as ex:
        log(f"TEST BASARISIZ: {type(ex).__name__}: {ex}")
        return 1


def main():
    if os.environ.get("SMC_TEST_MESAJI", "").lower() == "true":
        return baglanti_testi()

    state = state_yukle()
    acik = bist_acik_mi()
    if not acik:
        log("BIST kapali (seans disi) - tarama yapilmadi.")
        return 0

    for sira, ad in enumerate(TAKIP_LISTESI):
        if sira:
            time.sleep(ISTEK_ARASI_SN)
        onceki = state.get(ad, {})
        try:
            veri = KUR.veri_cek(ad)
        except Exception as ex:
            log(f"{ad}: VERI ALINAMADI ({type(ex).__name__}: {ex})")
            if not onceki.get("veri_sorunu"):
                try:
                    telegram_gonder(
                        f"⚠️ <b>{ad} — VERİ YOK</b>\n\n"
                        f"Seans açık ama veri alınamadı "
                        f"({type(ex).__name__}).\n\n"
                        f"<b>{ad} takibi durdu.</b> Bu hissede mesaj gelmemesi "
                        f"\"kurulum yok\" anlamına gelmez — sistem kör.")
                except Exception as ex2:
                    log(f"{ad}: uyari gonderilemedi: {ex2}")
            onceki["veri_sorunu"] = True
            state[ad] = onceki
            continue

        yas = bar_yasi_dk(veri["1h"])
        if yas > AZAMI_YAS_DK or len(veri["1h"]) < 60:
            log(f"{ad}: veri bayat ({yas:.0f}dk) veya kisa - atlandi")
            continue

        if onceki.get("veri_sorunu"):
            try:
                telegram_gonder(f"✅ <b>{ad} VERİSİ GERİ GELDİ</b>\n\n"
                                f"Takip normale döndü.")
            except Exception as ex:
                log(f"{ad}: duzelme bildirimi gonderilemedi: {ex}")

        r = degerlendir(ad, veri)
        eski_durum = onceki.get("durum", "BASLANGIC")
        eski_skor = onceki.get("skor", 0)

        # Ilk goruste sessiz kal: yeni bir hisse listeye eklendiginde
        # durumu zaten "BASLANGIC"tan degisir. 30 hisse icin bu, tek
        # seferde 30 mesaj demekti - hem okunmaz hem Telegram bogazlar.
        # Yalniz eyleme donuk durumlar ilk turda da bildirilir.
        ilk_gorus = eski_durum == "BASLANGIC"
        sessiz_ilk = ilk_gorus and r["durum"] in ("NOTR", "LONG_YOK")

        if r["durum"] != eski_durum and not sessiz_ilk:
            try:
                ok = telegram_gonder(mesaj_olustur(r, eski_durum, eski_skor))
                log(f"{ad}: {eski_durum} -> {r['durum']} (skor {r['skor']}) "
                    f"TELEGRAM={'OK' if ok else 'HATA'}")
            except Exception as ex:
                log(f"{ad}: Telegram gonderim hatasi: {ex}")
        elif sessiz_ilk:
            log(f"{ad}: ilk gorus {r['durum']} (skor {r['skor']}) - sessiz")
        else:
            log(f"{ad}: {r['durum']} degismedi "
                f"(skor {eski_skor}->{r['skor']}, fiyat {r['fiyat']:.2f})")

        p = r["plan"]
        state[ad] = {
            "veri_sorunu": False,
            "veri_yas_dk": round(yas),
            "durum": r["durum"],
            "skor": r["skor"],
            "fiyat": r["fiyat"],
            "yon": r["yon_smc"],
            "kurulum": r["kur_durum"],
            "plan": ({"giris": p["giris"], "stop": p["stop"],
                      "hedef": p["hedef"], "rr": round(p["rr"], 2)}
                     if p else None),
            "bolge": r["bolge"],
            "eq": r["eq"],
            "aralik": [r["aralik_dip"], r["aralik_tepe"]],
            "zaman_utc": r["zaman_utc"],
            "guncelleme": datetime.now(timezone.utc)
                                  .strftime("%Y-%m-%d %H:%M:%S UTC"),
        }

    state_kaydet(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
