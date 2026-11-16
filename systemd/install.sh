#!/bin/bash
# 安裝並啟動充電站 systemd 服務。用法： sudo bash systemd/install.sh
set -e
[ "$(id -u)" -eq 0 ] || { echo "請用 sudo 執行"; exit 1; }
DIR="$(cd "$(dirname "$0")" && pwd)"
install -m 644 "$DIR/can0.service"    /etc/systemd/system/can0.service
install -m 644 "$DIR/npb1700.service" /etc/systemd/system/npb1700.service
systemctl daemon-reload
systemctl enable --now can0.service
systemctl enable --now npb1700.service
sleep 3
echo "----- can0 -----";    ip -details link show can0 | grep -E 'state|bitrate' || true
echo "----- npb1700 -----"; systemctl --no-pager status npb1700.service | head -15
echo "----- /status -----"; curl -s http://localhost:8000/status || echo "(伺服器尚未回應)"
echo
