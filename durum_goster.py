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
print(f"{'ENSTRUMAN':10} {'DURUM':18} {'SKOR':>5}  {'KAYNAK':20} GUNCELLEME")
print("-" * 88)
for k, v in d.items():
    if k.startswith("_"):
        continue
    print(f"{k:10} {v.get('durum','-'):18} {v.get('skor','-'):>5}  "
          f"{v.get('kaynak','-'):20} {v.get('guncelleme','-')}")
o = d.get("_oran")
if o:
    print(f"\nNQ/QQQ orani: {o.get('NQ_QQQ')}")
