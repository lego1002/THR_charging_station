# 充電站 systemd 服務

| 服務 | 作用 |
|---|---|
| `can0.service` | 開機時把 can0 設成 250 kbps 並啟用 (bus-off 時 100 ms 自動恢復) |
| `npb1700.service` | 以 robot 使用者執行 `npb1700_server.py`，網頁埠 8000，當機 3 秒後自動重啟 |

安裝 (只需一次)：`sudo bash systemd/install.sh`

常用指令：
- 狀態：`systemctl status npb1700`
- 即時 log：`journalctl -u npb1700 -f`
- 重啟 (改完程式後)：`sudo systemctl restart npb1700`
- 停止：`sudo systemctl stop npb1700` (停止時會把繼電器關掉)
- 取消開機自動啟動：`sudo systemctl disable npb1700 can0`
- 看 CAN 原始封包：`candump can0` (需 `sudo apt install can-utils`)
