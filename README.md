# THR Charging Station / THR 自動充電站

[English](#english) · [中文](#中文)

---

<a id="english"></a>

# English — User Guide

This is the charging station for the THR greenhouse tomato-harvesting vehicle. The charger is a Mean Well **NPB-1700-48**, controlled by a Raspberry Pi.
All you need is a **phone or laptop**: open a web page in the browser to watch voltage and current, and to start or stop charging.

> No programming needed. Follow the steps below one by one.
> **If anything does not match this guide, stop right there and ask the person in charge.**

## 1. Power on

1. Plug in the **AC-to-DC power adapter** (the small box contains the Raspberry Pi, the CAN module and the relay; outside it is connected to the BTS7960 that drives the station's electromagnet).
2. Plug in the AC power of the **NPB-1700 charger**.
3. **Wait 1–2 minutes.** The station software starts by itself. **No monitor and no commands are needed.**

## 2. Connect to the network

After booting, the station joins the lab Wi-Fi **`RMML_5G`** automatically.

1. Connect your phone or laptop to **`RMML_5G`** as well (ask the lab manager for the password).
   **You must be on the same Wi-Fi as the station.** Other networks (e.g. mobile data, the BIME201 Wi-Fi) will not open the page.
2. Open a browser (Chrome or Safari) and type in the address bar:

   ```
   http://robot-desktop.local:8000
   ```

3. When you see a page titled "**NPB-1700 充電站**", you are connected.

> If the page does not open, see Section 7, "Troubleshooting".

## 3. What the web page shows

| On screen | Meaning |
|---|---|
| 連線 (Connection) green | Your device is connected to the station |
| CAN green | The station is reading data from the charger. **If it is red, do not charge** |
| 繼電器 (Relay) ON/OFF | Whether the station has *told* the charger to turn on. **ON does not mean it is actually charging**; check the current below |
| 電壓 (Voltage) | Voltage at the charger output (V) |
| 電流 (Current) | Current flowing into the battery (A). **Only above 0 means it is really charging** |
| 溫度 (Temperature) | Temperature **inside the charger**, **not** the copper contacts |
| 開始充電 / 停止充電 | Start / stop the charger |
| 放開電磁鐵 (2 秒) | Release the electromagnet for 2 seconds, so the charging head can be pulled out |

Built-in protection: the station **cuts power automatically** if voltage exceeds 59.5 V, current exceeds 26 A, the charger's own temperature exceeds 85 °C, or charger data can no longer be read.

## 4. Charging procedure (always in this order)

Rule: **connect first, then power on; power off first, then disconnect.** Never plug in or pull out the charging head while current is flowing.

### Start charging

1. Make sure the **contactor switch is OFF** (the contactor is mounted on the station base; for the demo it has an emergency-style switch wired out, and OFF = **pressed down**), and the web page shows Relay **OFF**.
2. Make sure the **red emergency-stop button is released** (popped up).
3. Insert the charging head into the dock and let the electromagnet hold it. **Check that the positive (+) marks line up** (inserting it upside down swaps + and −).
4. Turn the **contactor ON**.
5. The **voltage** on the web page should be between **42 and 58.8 V** (battery voltage).
   - About 0 V: the head is not seated properly, or the contactor is not on.
   - On first use, or after any rewiring, **use a multimeter on the charger output terminals, V+ to V−, and confirm the reading is positive**. If it is negative, the polarity is reversed: **turn the contactor OFF immediately and do not press Start.**
6. Press "**開始充電**" (Start charging).
7. Within a few seconds the **current** should rise (to about 25 A if the battery is not nearly full).
   - If Relay shows ON but current stays at 0 and the charger status reads `OP_OFF`, the **emergency stop is usually pressed**. Press "停止充電" (Stop) first, release the emergency stop, then press "開始充電" again.
8. During the first few minutes, check by hand or with an infrared thermometer whether the **copper contacts and wire terminals** are getting hot.

### Stop charging

1. Press "**停止充電**" (Stop charging).
2. Wait until the **current drops to about 0 A**.
3. Turn the **contactor OFF**.
4. Press "放開電磁鐵" (Release magnet) and pull out the charging head.

## 5. Emergency

If there is **smoke, a burning smell, sparks, or the copper is too hot to touch**:

1. **Hit the red emergency-stop button mounted on the charging dock.** It cuts power in hardware; no network or web page needed.
2. Turn the contactor OFF.
3. Tell the person in charge. **Do not turn it on again until the cause is found.**

## 6. Shutting down

1. Finish "Stop charging" in Section 4 first.
2. Unplug the charger's AC power.
3. The Raspberry Pi can simply be unplugged (but not while charging).

## 7. Troubleshooting

| Problem | What to do |
|---|---|
| The page will not open | ① Check that your phone is on `RMML_5G`. Tip (outside the lab): set your phone hotspot's name to `RMML_5G` with exactly the same password as the lab Wi-Fi, and the Raspberry Pi will join it. ② The station needs 1–2 minutes after power-on. ③ Some Android phones do not support `.local` addresses; use a laptop instead, or ask the person in charge (Lego) to control it remotely |
| After pressing Start, the page says "無法啟動：電池電壓…不在…" (battery voltage out of range) | The charging head is not seated, or the contactor is not on |
| "無法啟動：充電器 CAN 無回應" (CAN no response) | The charger's AC is not plugged in, or the CAN cable is loose. Tell the person in charge |
| "無法啟動：電池充滿信號為高" (battery-full signal high) | The battery is already full; no need to charge |
| Current is small and soon drops to 0 | The battery is nearly full. This is normal |
| Relay ON but current stays at 0 | The emergency stop is pressed (see Section 4, step 7) |

## For maintainers

| File | Purpose |
|---|---|
| `npb1700_server.py` | Main program (FastAPI, web port 8000): CAN monitoring, GPIO relay, electromagnet control, safety cut-off |
| `npb1700_rpi.py` | Older command-line monitoring script |
| `npb1700_can_reference.md` | NPB-1700 CAN command quick reference |
| `NPB1700_usermanual_CAN.pdf` | Mean Well CAN manual |
| `systemd/` | Auto-start services; see `systemd/README.md` to install |

- API: `GET /status`, `POST /charge/on`, `POST /charge/off`, `POST /magnet/release?ms=2000`, `POST /magnet/off`, WebSocket `/ws`
- Hardware: Raspberry Pi (Ubuntu 22.04) + MCP2515 CAN (250 kbps, 120 Ω termination); GPIO22 (physical pin 15) → relay → NPB pins 7/8 (in series with a normally-closed emergency stop)
- With the default DIP settings the NPB-1700 **ignores CAN writes**, so CAN is used for monitoring only; on/off is done by the hardware relay.
- Logs: `journalctl -u npb1700 -f`; restart after editing code: `sudo systemctl restart npb1700`

---

<a id="中文"></a>

# 中文 — 使用說明

溫室番茄採收車（THR）的充電站。充電器是明緯 **NPB-1700-48**，由一台 Raspberry Pi 控制。
你只需要一支**手機或筆電**，用瀏覽器打開網頁，就可以看電壓、電流，以及開始／停止充電。

> 不用會寫程式，照著下面的步驟一步一步做就可以。
> **有任何一步跟說明不一樣，就先停下來，不要繼續做，然後問負責人。**

## 1. 開機

1. 插上**市電轉直流電的變壓器**電源（小盒子裡面有樹莓派、CAN 模組和繼電器，外面連接著控制充電站電磁鐵的 BTS7960）。
2. 插上**充電器 NPB-1700** 的 AC 電源。
3. **等 1～2 分鐘。** 充電站的程式會自己啟動，**不用接螢幕、也不用打任何指令**。

## 2. 連上網路

充電站開機後會自動連上實驗室的 Wi-Fi **`RMML_5G`**。

1. 把你的手機或筆電也連上 **`RMML_5G`**（密碼請問實驗室負責人）。
   **一定要和充電站連在同一個 Wi-Fi。** 連其他網路（例如 4G、BIME201 的 Wi-Fi）會打不開網頁。
2. 打開瀏覽器（Chrome、Safari 都可以），在網址列輸入：

   ```
   http://robot-desktop.local:8000
   ```

3. 看到標題寫著「**NPB-1700 充電站**」的網頁，就表示連上了。

> 打不開的話，請看第 7 節「常見問題」。

## 3. 網頁上各個東西是什麼意思

| 畫面上寫的 | 意思 |
|---|---|
| 連線 綠色 | 你的手機和充電站連線正常 |
| CAN 綠色 | 充電站有讀到充電器的資料。**如果是紅色，就不要充電** |
| 繼電器 ON／OFF | 充電站「有沒有下指令」叫充電器開機。**ON 不代表真的在充電**，要看下面的電流 |
| 電壓 | 充電器輸出端的電壓（V） |
| 電流 | 正在流進電池的電流（A）。**大於 0 才是真的在充電** |
| 溫度 | **充電器內部**的溫度，**不是**銅片的溫度 |
| 開始充電／停止充電 | 打開／關閉充電器 |
| 放開電磁鐵 (2 秒) | 讓電磁鐵鬆開充電頭 2 秒，用來拔開充電頭 |

充電站有自動保護：電壓超過 59.5 V、電流超過 26 A、充電器本身溫度超過 85 °C，或讀不到充電器資料時，會**自動斷電**。

## 4. 充電步驟（一定要照這個順序）

原則：**先接好、再通電；先斷電、再拔開。** 有電流在跑的時候，不能接上或拔開充電頭。

### 開始充電

1. 確認**接觸器的開關是 OFF**（接觸器外掛在充電站底座；為了 Demo 另外拉出一組緊急開關，**按下去＝OFF**），網頁上繼電器顯示 **OFF**。
2. 確認**紅色急停按鈕是放開的**（彈起來的狀態）。
3. 把充電頭插進充電座，讓電磁鐵吸住。**先確認正極標記有對齊**（反插會讓正負極對調）。
4. 打開**接觸器**。
5. 看網頁上的**電壓**，應該要在 **42～58.8 V** 之間（電池電壓）。
   - 如果是 0 V 左右：表示沒接好，或接觸器沒有打開。
   - 第一次使用、或有重新接過線，要**用三用電表量充電器的輸出端子 V+ 對 V−，確認是正電壓**。如果是負的，就表示極性接反了，**馬上關掉接觸器，不要按開始充電。**
6. 按「**開始充電**」。
7. 幾秒內**電流**應該要開始上升（電池沒滿的話，會到 25 A 左右）。
   - 如果繼電器顯示 ON，但電流一直是 0、充電器狀態寫 `OP_OFF`，通常是**急停按鈕被壓下去了**。請先按「停止充電」，把急停放開，再按一次「開始充電」。
8. 剛開始充電的前幾分鐘，用手或紅外線溫度計摸摸看**銅片和接線端子**有沒有發燙。

### 結束充電

1. 按「**停止充電**」。
2. 等**電流降到 0 A 左右**。
3. 關掉**接觸器**。
4. 按「放開電磁鐵」，拔出充電頭。

## 5. 緊急情況

有**冒煙、焦味、火花，或銅片燙到摸不住**時：

1. **直接拍固定在充電座上的紅色急停按鈕。** 急停是硬體直接斷電，不需要網路，也不需要網頁。
2. 關掉接觸器。
3. 通知負責人。**沒有找到原因之前，不要再開。**

## 6. 關機

1. 先照第 4 節的「結束充電」做完。
2. 拔掉充電器的 AC 電源。
3. Raspberry Pi 可以直接拔電源（不建議在充電中拔）。

## 7. 常見問題

| 狀況 | 怎麼辦 |
|---|---|
| 網頁打不開 | ① 確認手機連的是 `RMML_5G`。小技巧（在實驗室外）：把手機熱點的名稱設成 `RMML_5G`，密碼跟實驗室的一模一樣，樹莓派就會自動連上。② 充電站開機要等 1～2 分鐘。③ 部分 Android 手機不支援 `.local` 網址，請改用筆電，或請負責人（樂高）遠端連線控制 |
| 按了開始充電，網頁說「無法啟動：電池電壓不在範圍內」 | 充電頭沒有插好，或接觸器沒有打開 |
| 「無法啟動：CAN 無回應」 | 充電器沒有插 AC 電源，或 CAN 線鬆了，請通知負責人 |
| 「無法啟動：電池充滿信號為高」 | 電池已經滿了，不需要充電 |
| 電流很小、很快就降到 0 | 電池快滿了，這是正常的 |
| 繼電器 ON，但電流一直是 0 | 急停按鈕被壓下去了（見第 4 節第 7 步） |

## 給維護者

| 檔案 | 用途 |
|---|---|
| `npb1700_server.py` | 主程式（FastAPI，網頁 port 8000）：CAN 監控、GPIO 繼電器、電磁鐵控制、安全斷電 |
| `npb1700_rpi.py` | 舊版命令列監控程式 |
| `npb1700_can_reference.md` | NPB-1700 CAN 指令速查表 |
| `NPB1700_usermanual_CAN.pdf` | 明緯原廠 CAN 手冊 |
| `systemd/` | 開機自動啟動的服務設定，安裝方式見 `systemd/README.md` |

- API：`GET /status`、`POST /charge/on`、`POST /charge/off`、`POST /magnet/release?ms=2000`、`POST /magnet/off`、WebSocket `/ws`
- 硬體：Raspberry Pi（Ubuntu 22.04）+ MCP2515 CAN（250 kbps，120 Ω 終端）；GPIO22（實體 Pin 15）→ 繼電器 → NPB Pin 7/8（串接常閉急停）
- NPB-1700 在預設 DIP 設定下會**忽略 CAN 寫入**，所以 CAN 只用來監控，開關機靠硬體繼電器。
- 看 log：`journalctl -u npb1700 -f`；改完程式後要重啟：`sudo systemctl restart npb1700`
