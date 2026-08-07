# -*- coding: utf-8 -*-
"""GitHub'daki guncel durumu okunakli gosterir."""
import json, subprocess, sys, os

os.chdir(r"C:\Users\USER\smc-watcher")
try:
    ham = subprocess.check_output(
        ["git", "show", "origin/main:smc_state_auto.json"], text=True, encoding="utf-8")
except Exception as e:
    print("okunamadi:", e); sys.exit(1)

d = json.loads(ham)
print(f"{'ENSTRUMAN':10} {'DURUM':16} {'SKOR':>5}  {'PLAN (giris/stop/hedef)':34} "
      f"{'KAYNAK':16} GUNCELLEME")
print("-" * 120)
for k, v in d.items():
    if k.startswith("_"):
        continue
    p = v.get("plan")
    plan = (f"{p['giris']:.2f} / {p['stop']:.2f} / {p['hedef']:.2f}  RR {p['rr']}"
            if p else "-")
    print(f"{k:10} {v.get('durum','-'):16} {v.get('skor','-'):>5}  {plan:34} "
          f"{v.get('kaynak','-'):16} {v.get('guncelleme','-')}")
o = d.get("_oran")
if o:
    print(f"\nNQ/QQQ orani: {o.get('NQ_QQQ')}")
