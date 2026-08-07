#!/bin/bash
# SMC Watcher - Oracle Cloud / Ubuntu sunucu kurulumu
# Kullanim: sunucuda  bash sunucu_kurulum.sh
set -e

echo "=== 1/6  Saat dilimi: Europe/Istanbul ==="
sudo timedatectl set-timezone Europe/Istanbul
date

echo
echo "=== 2/6  Python kontrolu ==="
if ! command -v python3 >/dev/null 2>&1; then
    sudo apt-get update -qq
    sudo apt-get install -y python3
fi
python3 --version
# NOT: smc_watch.py sadece standart kutuphane kullanir, pip paketi gerekmez.

echo
echo "=== 3/6  Klasor ==="
mkdir -p ~/smc-watcher
cd ~/smc-watcher
ls -la

echo
echo "=== 4/6  Dosya kontrolu ==="
eksik=0
for f in smc_watch.py telegram_config.json; do
    if [ -f "$f" ]; then
        echo "  var : $f"
    else
        echo "  EKSIK: $f"
        eksik=1
    fi
done
if [ "$eksik" = "1" ]; then
    echo "Dosyalar eksik. Once scp ile kopyalanmali. Cikiliyor."
    exit 1
fi
chmod 600 telegram_config.json   # token'i sadece sahibi okusun

echo
echo "=== 5/6  Test calistirma ==="
python3 smc_watch.py

echo
echo "=== 6/6  Cron kurulumu (15 dakikada bir) ==="
SATIR="*/15 * * * * cd \$HOME/smc-watcher && /usr/bin/python3 smc_watch.py >> cron.log 2>&1"
# ayni satir varsa tekrar ekleme
( crontab -l 2>/dev/null | grep -v "smc_watch.py" ; echo "$SATIR" ) | crontab -
echo "Cron tablosu:"
crontab -l

echo
echo "=== KURULUM TAMAM ==="
echo "Log: ~/smc-watcher/smc_watch.log"
echo "Cron log: ~/smc-watcher/cron.log"
echo "Durum: ~/smc-watcher/smc_state_auto.json"
