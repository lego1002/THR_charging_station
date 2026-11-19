#!/usr/bin/env python3
"""
NPB-1700-48 充電站服務 v7 (FastAPI + WebSocket + 控制)

網頁可即時監測 + 遠端開關充電器。
- 監控：CAN Bus 讀取電壓/電流/溫度/故障/充電曲線設定 (背景每秒輪詢)
- 控制：GPIO 繼電器 (Pin 7/8 Remote ON/OFF)
- 安全：CAN 故障旗標 + 軟體閾值 + CAN 斷線偵測 觸發繼電器斷電
        + 實體急停按鈕 (串聯在繼電器迴路)

v7 變更 (相對 v6，舊版在 backup/npb1700_server_v6.py)：
- 過壓門檻 58.0 → 59.5 V (電池滿充 58.8 V，舊值會在 CV 階段誤跳)
- 過流門檻 40 → 26 A (NPB-1700-48 最大 25 A，舊值永遠不會觸發)
- CAN 無回應時拒絕開啟；充電中 CAN 斷線超過 CAN_TIMEOUT 自動斷電
- 開啟前要求「新鮮」的 CAN 回覆 (故障、電壓、CV 設定)，不用舊資料
- 開啟前檢查電池電壓在合理範圍、充電器 CV 設定不超過電池滿充電壓
- BAT_OK/CHG_OK 輸入加下拉電阻 + 連續 2 次才判定，避免浮接誤判
- 溫度改為有號數解析 (零下不會被當成幾千度)
- CAN 送出間隔至少 25 ms (手冊最小 20 ms)，監控與 API 共用不打架
- 自保型電磁鐵控制 (BTS7960)：POST /magnet/release?ms=5000、POST /magnet/off
  通電上限 5 秒、冷卻 5 秒、監控執行緒備援強制斷電
- 電磁鐵放開互鎖：充電中、仍有電流、或銅片仍帶電 (車上接觸器未斷開) 時拒絕放開並警告；
  CAN 無回應 (無法確認是否帶電) 時也拒絕。電磁鐵放開期間不允許開始充電

接線：
  RPi GPIO22 (Pin15) → Relay IN
  NPB Pin7 → 急停按鈕 → Relay COM/NO → NPB Pin8 (串聯)

安裝套件 (以 robot 使用者)：
  pip3 install fastapi "uvicorn[standard]" python-can lgpio

啟動：
  正式：sudo systemctl start npb1700   (見 systemd/README.md)
  手動：python3 npb1700_server.py      (需先 can0 up，且使用者有 GPIO 權限)

瀏覽器：http://<樹莓派 IP>:8000
"""

import asyncio
import json
import subprocess
import threading
import time

import lgpio
import can

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import uvicorn

# ======================== GPIO ========================
PIN_BAT_OK = 17    # Pin 11 ← NPB Pin 5 (經分壓)
PIN_CHG_OK = 27    # Pin 13 ← NPB Pin 6 (經分壓)
PIN_RELAY  = 22    # Pin 15 → Relay IN

# 自保型電磁鐵 (XDA-30/25D) 經 BTS7960：斷電=吸住，通電=放開
# 兩腳同時為高才通電；兩腳外接 10k 下拉，開機/當機時保持吸住
PIN_MAG_DIR = 23   # Pin 16 → BTS7960 RPWM (LPWM 接 GND)
PIN_MAG_EN  = 24   # Pin 18 → BTS7960 R_EN + L_EN (短接)

# ======================== CAN ========================
CAN_CHANNEL = 'can0'
DEVICE_ADDR = 3
TX_ID = 0x000C0100 | DEVICE_ADDR
RX_ID = 0x000C0000 | DEVICE_ADDR
CAN_TX_GAP = 0.025   # 送出最小間隔 (手冊: 20 ms)

CMD = {
    'FAULT_STATUS': 0x0040,
    'READ_VOUT':    0x0060,
    'READ_IOUT':    0x0061,
    'READ_TEMP':    0x0062,
    'CURVE_CC':     0x00B0,
    'CURVE_CV':     0x00B1,
    'CHG_STATUS':   0x00B8,
}
POLL_CMDS  = ['READ_VOUT', 'READ_IOUT', 'READ_TEMP', 'FAULT_STATUS', 'CHG_STATUS']
CURVE_CMDS = ['CURVE_CV', 'CURVE_CC']
CURVE_POLL_EVERY = 30   # 每 30 秒讀一次曲線設定

# FAULT_STATUS 位元
F_OTP, F_OVP, F_OLP, F_SHORT, F_AC_FAIL, F_OP_OFF, F_HI_TEMP = 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80

# ======================== 安全閾值 ========================
VOLTAGE_MAX = 59.5    # 過壓保護 (V)；電池滿充 58.8 V，留 0.7 V 給量測誤差 (手冊 ±0.48 V)
CURRENT_MAX = 26.0    # 過流保護 (A)；NPB-1700-48 最大 25 A，設計持續 25 A，保險絲 30 A
TEMP_MAX    = 85.0    # 過溫保護 (°C)
CAN_TIMEOUT = 3.0     # 超過這麼久沒收到 CAN 回覆 → 視為斷線 (秒)

# 開啟前電池檢查 (流程第 5 步)
REQUIRE_BAT_VOLTAGE = True   # 若實測發現充電器 OFF 時讀不到電池電壓，可暫時改 False
BAT_V_MIN = 42.0             # 14S × 3.0 V；低於此值視為沒接上電池或電池異常
BAT_V_MAX = 58.8             # 14S × 4.2 V 滿充
CURVE_CV_TOL = 0.05          # CV 設定允許超出 BAT_V_MAX 的量 (V)

# 電磁鐵 (PLA 座約 55–60 °C 軟化，通電時間必須受限)
# 實測 5 秒通電不會明顯發燙
MAG_RELEASE_S  = 5.0    # 預設放開時間 (秒)
MAG_MAX_S      = 5.0    # 單次通電上限 (秒)；API 超過即拒絕
MAG_WATCHDOG_S = MAG_MAX_S + 0.5   # 監控執行緒備援強制斷電 (多留 0.5 秒避免和正常結束搶)
MAG_COOLDOWN_S = 5.0    # 兩次放開之間最少間隔 (秒)

# 放開電磁鐵互鎖：充電頭必須確實不帶電才可放開
# 接觸器 OFF + 充電器 OFF 時銅片電壓應約 0 V；門檻遠低於電池最低 42 V
# 【待確認】實測接觸器斷開後讀值多少、充電器輸出電容放電要多久
SAFE_RELEASE_V_MAX = 10.0   # 電壓高於此值 → 視為帶電，不可放開 (V)
SAFE_RELEASE_I_MAX = 0.5    # 電流高於此值 → 視為仍在充電，不可放開 (A)

# ======================== 狀態 ========================
class State:
    voltage = 0.0; current = 0.0; temp = 0.0
    fault = 0; chg_st = 0
    curve_cv = None; curve_cc = None   # 充電器內的 CV/CC 設定 (未讀到為 None)
    bat_ok = False; chg_ok = False
    bat_ok_cnt = 0
    charging = False
    can_rx = 0; faults = 0
    last_rx = 0.0      # 最近一次收到回覆的 monotonic 時間
    rx_t = {}          # 每個指令最近一次回覆的 monotonic 時間
    t_start = None; t_total = 0
    last_event = ""    # 最近一次事件訊息 (給網頁顯示)
    mag_on = False     # 電磁鐵是否通電中 (=放開中)
    mag_t_on = 0.0     # 通電開始 (monotonic)
    mag_t_off = 0.0    # 上次斷電 (monotonic)，用於冷卻
    mag_count = 0      # 累計放開次數

s = State()
bus = None
chip = None
lock = threading.Lock()        # 保護量測資料
ctl_lock = threading.RLock()   # 保護繼電器/充電狀態 (API 與監控執行緒共用)
tx_lock = threading.Lock()     # CAN 送出排隊
mag_lock = threading.Lock()    # 電磁鐵 (同時只允許一個放開動作)
_last_tx = 0.0

# ======================== CAN ========================
def can_read(cmd_code):
    """送出讀取請求；自動保持最小送出間隔"""
    global _last_tx
    if bus is None:
        return False
    msg = can.Message(arbitration_id=TX_ID, is_extended_id=True,
        data=[cmd_code & 0xFF, (cmd_code >> 8) & 0xFF])
    with tx_lock:
        wait = CAN_TX_GAP - (time.monotonic() - _last_tx)
        if wait > 0:
            time.sleep(wait)
        try:
            bus.send(msg)
            ok = True
        except Exception:
            ok = False
        _last_tx = time.monotonic()
    return ok

def parse_reply(msg):
    if msg.arbitration_id != RX_ID or msg.dlc < 2:
        return
    cmd = msg.data[0] | (msg.data[1] << 8)
    val = 0
    if msg.dlc >= 4:
        val = msg.data[2] | (msg.data[3] << 8)
    elif msg.dlc >= 3:
        val = msg.data[2]
    now = time.monotonic()
    with lock:
        s.can_rx += 1
        s.last_rx = now
        s.rx_t[cmd] = now
        if   cmd == CMD['READ_VOUT']:    s.voltage = val * 0.01
        elif cmd == CMD['READ_IOUT']:    s.current = val * 0.01
        elif cmd == CMD['READ_TEMP']:
            if val >= 0x8000:            # 有號 16-bit
                val -= 0x10000
            s.temp = val * 0.1
        elif cmd == CMD['FAULT_STATUS']: s.fault = val
        elif cmd == CMD['CHG_STATUS']:   s.chg_st = val
        elif cmd == CMD['CURVE_CV']:     s.curve_cv = val * 0.01
        elif cmd == CMD['CURVE_CC']:     s.curve_cc = val * 0.01

def can_rx_thread():
    while True:
        try:
            msg = bus.recv(timeout=1.0)
            if msg:
                parse_reply(msg)
        except Exception:
            time.sleep(0.5)   # 介面 down 時避免空轉

def can_fresh():
    """CAN 是否在 CAN_TIMEOUT 內有回覆"""
    return bus is not None and s.last_rx > 0 and time.monotonic() - s.last_rx < CAN_TIMEOUT

def request_fresh(names, timeout=0.6):
    """送出多個讀取請求，等到每個都收到「送出之後」的新回覆。回傳是否全部收到"""
    t0 = time.monotonic()
    for n in names:
        can_read(CMD[n])
    deadline = t0 + timeout
    while time.monotonic() < deadline:
        if all(s.rx_t.get(CMD[n], 0) > t0 for n in names):
            return True
        time.sleep(0.01)
    return False

def poll_all():
    for n in POLL_CMDS:
        can_read(CMD[n])

def monitor_thread():
    """背景每秒輪詢 + 安全檢查 (CAN 失敗時也照常執行安全檢查)"""
    n = 0
    while True:
        try:
            if bus:
                poll_all()
                if n % CURVE_POLL_EVERY == 0:
                    for c in CURVE_CMDS:
                        can_read(CMD[c])
            read_gpio()
            safety_check()
            magnet_watchdog()
        except Exception as e:
            print(f"  monitor 錯誤: {e}")
        n += 1
        time.sleep(1.0)

# ======================== GPIO ========================
def gpio_init():
    global chip
    chip = lgpio.gpiochip_open(0)
    for pin in (PIN_BAT_OK, PIN_CHG_OK):
        try:
            # 下拉：分壓線路斷掉/沒接時讀 0 (不會誤判為電池已滿)
            lgpio.gpio_claim_input(chip, pin, lgpio.SET_PULL_DOWN)
        except Exception as e:
            print(f"  ⚠ GPIO{pin} 無法設定下拉 ({e})，改用無偏壓輸入")
            lgpio.gpio_claim_input(chip, pin)
    lgpio.gpio_claim_output(chip, PIN_RELAY, 0)
    # 電磁鐵兩腳一開始就是 0 (吸住)
    lgpio.gpio_claim_output(chip, PIN_MAG_EN, 0)
    lgpio.gpio_claim_output(chip, PIN_MAG_DIR, 0)

def release_outputs_safe():
    """關閉前：輸出腳先寫 0，再改成「輸入 + 下拉」，服務停止後也確定是低電位
    (電磁鐵吸住、充電器繼電器 OFF)"""
    for pin in (PIN_MAG_DIR, PIN_MAG_EN, PIN_RELAY):
        try:
            lgpio.gpio_write(chip, pin, 0)
            lgpio.gpio_free(chip, pin)
            lgpio.gpio_claim_input(chip, pin, lgpio.SET_PULL_DOWN)
        except Exception as e:
            print(f"  ⚠ GPIO{pin} 無法切換為輸入+下拉 ({e})")

def read_gpio():
    try:
        bat = bool(lgpio.gpio_read(chip, PIN_BAT_OK))
        s.chg_ok = bool(lgpio.gpio_read(chip, PIN_CHG_OK))
    except Exception:
        return
    # 去彈跳：連續 2 次 (約 2 秒) 為高才算電池已滿
    s.bat_ok_cnt = s.bat_ok_cnt + 1 if bat else 0
    s.bat_ok = s.bat_ok_cnt >= 2

# ======================== 控制 ========================
def _refuse(msg):
    s.last_event = msg
    return False, msg

def charger_on():
    """開啟充電器。回傳 (成功?, 訊息)"""
    with ctl_lock:
        if s.charging:
            return False, "Already charging"
        if s.mag_on:
            return _refuse("Cannot start: magnet is released, charging head not held")
        if bus is None:
            return _refuse("Cannot start: CAN not connected, cannot monitor")

        # 取得新鮮資料 (不能用舊值判斷)
        if not request_fresh(['FAULT_STATUS', 'READ_VOUT', 'CURVE_CV', 'CURVE_CC']):
            return _refuse("Cannot start: charger not responding on CAN")

        fault_bits = s.fault & ~F_OP_OFF   # 忽略 OP_OFF (還沒開當然是 OFF)
        if fault_bits:
            return _refuse(f"Cannot start: charger fault {', '.join(fmt_fault(fault_bits))}")

        # 充電曲線 CV 不可高於電池滿充電壓
        if s.curve_cv is not None and s.curve_cv > BAT_V_MAX + CURVE_CV_TOL:
            return _refuse(f"Cannot start: charger CV setting {s.curve_cv:.2f} V is above battery max {BAT_V_MAX} V")

        # 流程第 5 步：確認電池電壓 (接觸器 ON 之後銅片才帶電池電壓)
        if REQUIRE_BAT_VOLTAGE and not (BAT_V_MIN <= s.voltage <= BAT_V_MAX):
            return _refuse(f"Cannot start: battery voltage {s.voltage:.2f} V not within {BAT_V_MIN}–{BAT_V_MAX} V "
                           f"(head not docked or contactor OFF?)")

        read_gpio(); read_gpio()
        if s.bat_ok:
            return _refuse("Cannot start: battery-full signal is high")

        lgpio.gpio_write(chip, PIN_RELAY, 1)
        s.charging = True
        s.t_start = time.time()
        msg = f"Charger ON (battery {s.voltage:.2f} V"
        if s.curve_cv is not None:
            msg += f", CV setting {s.curve_cv:.2f} V"
        msg += ")"
        s.last_event = msg
        return True, msg

def charger_off():
    """關閉充電器。回傳 (成功?, 訊息)"""
    with ctl_lock:
        lgpio.gpio_write(chip, PIN_RELAY, 0)
        if s.charging and s.t_start:
            s.t_total += int(time.time() - s.t_start)
        s.charging = False
        s.t_start = None
        s.last_event = "Charger OFF"
        return True, "Charger OFF"

def emergency_off(reason):
    with ctl_lock:
        s.faults += 1
        print(f"\n  ⚠ Emergency OFF: {reason} (#{s.faults})")
        charger_off()
        s.last_event = f"⚠ Emergency OFF: {reason}"   # charger_off 會覆蓋,重設回來

# ======================== 電磁鐵 ========================
def magnet_off():
    """斷電 = 吸住。任何情況都可呼叫，不會被鎖擋住"""
    try:
        lgpio.gpio_write(chip, PIN_MAG_DIR, 0)
        lgpio.gpio_write(chip, PIN_MAG_EN, 0)
    finally:
        if s.mag_on:
            s.mag_t_off = time.monotonic()
        s.mag_on = False

def magnet_live_reason():
    """依目前資料判斷充電頭是否可能帶電。回傳原因字串 (帶電/無法確認)，安全則回傳 None"""
    if s.charging:
        return "charging, stop charging first"
    if not can_fresh():
        return "CAN not responding, cannot confirm the head is dead"
    if s.current > SAFE_RELEASE_I_MAX:
        return f"current still {s.current:.2f} A, wait until it drops to 0"
    if s.voltage > SAFE_RELEASE_V_MAX:
        return f"charging head is live ({s.voltage:.1f} V), turn the contactor OFF first"
    return None

def magnet_release(seconds=MAG_RELEASE_S):
    """通電放開電磁鐵 seconds 秒後自動斷電 (阻塞到結束)。回傳 (成功?, 訊息)"""
    if not (0.1 <= seconds <= MAG_MAX_S):
        return False, f"Release time must be 0.1–{MAG_MAX_S:.0f} s"
    if not mag_lock.acquire(blocking=False):
        return False, "Magnet is already releasing"
    try:
        wait = MAG_COOLDOWN_S - (time.monotonic() - s.mag_t_off)
        if s.mag_t_off > 0 and wait > 0:
            return False, f"Magnet cooling down, try again in {wait:.1f} s"
        # 互鎖：持有 ctl_lock，檢查到通電之間不會有人開啟充電
        with ctl_lock:
            # 用「新鮮」的電壓/電流判斷，不用舊資料
            if bus is None or not request_fresh(['READ_VOUT', 'READ_IOUT']):
                reason = "CAN not responding, cannot confirm the head is dead"
            else:
                reason = magnet_live_reason()
            if reason:
                print(f"\n  ⚠ Magnet release refused: {reason}")
                return False, f"⚠ Cannot release magnet: {reason}"
            s.mag_on = True
            s.mag_t_on = time.monotonic()
            s.mag_count += 1
            lgpio.gpio_write(chip, PIN_MAG_EN, 1)
            lgpio.gpio_write(chip, PIN_MAG_DIR, 1)
        s.last_event = f"Releasing magnet ({seconds:.1f} s)"
        time.sleep(seconds)
        return True, f"Magnet released for {seconds:.1f} s, now holding again"
    finally:
        magnet_off()
        if s.last_event.startswith("Releasing magnet"):
            s.last_event = "Magnet holding again"
        mag_lock.release()

def magnet_watchdog():
    """備援：通電超過上限就強制斷電 (正常情況 magnet_release 會自己關)"""
    if s.mag_on and time.monotonic() - s.mag_t_on > MAG_WATCHDOG_S:
        magnet_off()
        s.faults += 1
        s.last_event = f"⚠ Magnet powered over {MAG_MAX_S:.0f} s, forced OFF"
        print(f"\n  {s.last_event}")

def safety_check():
    """基於 CAN 故障旗標 + 軟體閾值 + CAN 斷線的安全檢查 (只在充電中檢查)"""
    with ctl_lock:
        if not s.charging:
            return

        # CAN 斷線 → 無法監控，立即斷電
        if not can_fresh():
            emergency_off(f"CAN lost for over {CAN_TIMEOUT:.0f} s"); return

        # GPIO: 電池充滿信號
        if s.bat_ok:
            print("  ● Battery full, charging stopped automatically")
            charger_off()
            s.last_event = "Battery full, charging stopped automatically"
            return

        # CAN 故障旗標
        if s.fault & F_OTP:     emergency_off("OTP over-temperature"); return
        if s.fault & F_HI_TEMP: emergency_off("HI_TEMP internal high temperature"); return
        if s.fault & F_OVP:     emergency_off("OVP over-voltage"); return
        if s.fault & F_OLP:     emergency_off("OLP overload"); return
        if s.fault & F_SHORT:   emergency_off("SHORT short circuit"); return
        if s.fault & F_AC_FAIL: emergency_off("AC_FAIL AC input failure"); return

        # 軟體閾值保護
        if s.voltage > VOLTAGE_MAX: emergency_off(f"Voltage too high {s.voltage:.2f} V"); return
        if s.current > CURRENT_MAX: emergency_off(f"Current too high {s.current:.2f} A"); return
        if s.temp > TEMP_MAX:       emergency_off(f"Temperature too high {s.temp:.1f} °C"); return

# ======================== 格式化 ========================
def fmt_fault(f):
    fl = []
    if f & F_HI_TEMP: fl.append("HI_TEMP")
    if f & F_OP_OFF:  fl.append("OP_OFF")
    if f & F_AC_FAIL: fl.append("AC_FAIL")
    if f & F_SHORT:   fl.append("SHORT")
    if f & F_OLP:     fl.append("OLP")
    if f & F_OVP:     fl.append("OVP")
    if f & F_OTP:     fl.append("OTP")
    return fl if fl else ["None"]

def fmt_chg(c):
    hi = (c >> 8) & 0xFF
    lo = c & 0xFF
    parts = []
    if lo & 0x01: parts.append("FULL")
    if lo & 0x02: parts.append("CC")
    if lo & 0x04: parts.append("CV")
    if lo & 0x08: parts.append("Float")
    if hi & 0x08: parts.append("BTNC")
    return parts if parts else ["Idle"]

def snapshot():
    with lock:
        real_fault = s.fault & ~F_OP_OFF   # 忽略 OP_OFF
        sec = s.t_total + (int(time.time() - s.t_start) if s.charging and s.t_start else 0)
        return {
            "voltage": round(s.voltage, 2),
            "current": round(s.current, 2),
            "temp": round(s.temp, 1),
            "fault": fmt_fault(s.fault),
            "has_fault": bool(real_fault),
            "chg_status": fmt_chg(s.chg_st),
            "curve_cv": None if s.curve_cv is None else round(s.curve_cv, 2),
            "curve_cc": None if s.curve_cc is None else round(s.curve_cc, 2),
            "bat_ok": s.bat_ok,
            "charging": s.charging,
            "can_ok": can_fresh(),
            "can_rx": s.can_rx,
            "faults": s.faults,
            "elapsed": sec,
            "last_event": s.last_event,
            "magnet_on": s.mag_on,
            "magnet_count": s.mag_count,
            "magnet_lock": None if s.mag_on else magnet_live_reason(),   # 不可放開的原因 (None=可放開)
            "magnet_cooldown": round(max(0.0, MAG_COOLDOWN_S - (time.monotonic() - s.mag_t_off))
                                     if s.mag_t_off > 0 and not s.mag_on else 0.0, 1),
            "ts": int(time.time()),
        }

# ======================== FastAPI ========================
app = FastAPI(title="NPB-1700 充電站")

@app.get("/status")
def get_status():
    return snapshot()

@app.post("/charge/on")
def api_charge_on():
    ok, msg = charger_on()
    return {"ok": ok, "message": msg, "state": snapshot()}

@app.post("/charge/off")
def api_charge_off():
    ok, msg = charger_off()
    return {"ok": ok, "message": msg, "state": snapshot()}

@app.post("/magnet/release")
def api_magnet_release(ms: int = int(MAG_RELEASE_S * 1000)):
    """通電放開電磁鐵 ms 毫秒 (預設 5000，上限 5000)，結束後才回應。帶電時拒絕"""
    ok, msg = magnet_release(ms / 1000.0)
    if not ok:
        s.last_event = msg
    return {"ok": ok, "message": msg, "state": snapshot()}

@app.post("/magnet/off")
def api_magnet_off():
    """強制斷電 (= 吸住)"""
    magnet_off()
    s.last_event = "Magnet forced OFF (holding)"
    return {"ok": True, "message": s.last_event, "state": snapshot()}

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            await ws.send_text(json.dumps(snapshot()))
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass

@app.get("/", response_class=HTMLResponse)
def dashboard():
    return DASHBOARD_HTML

# ======================== 網頁 ========================
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NPB-1700 Charging Station</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, "Segoe UI", "Noto Sans TC", sans-serif;
    background: #0f1115; color: #e8eaed;
    padding: 20px; max-width: 720px; margin: 0 auto;
  }
  h1 { font-size: 20px; font-weight: 600; margin-bottom: 4px; }
  .sub { color: #9aa0a6; font-size: 13px; margin-bottom: 20px; }
  .conn { display: inline-block; padding: 3px 10px; border-radius: 12px;
          font-size: 12px; font-weight: 600; }
  .conn.ok  { background: #1e3a2a; color: #6ee7a0; }
  .conn.off { background: #3a1e1e; color: #f08a8a; }
  .grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; margin-bottom: 16px; }
  .card { background: #1a1d23; border: 1px solid #2a2e37; border-radius: 12px; padding: 18px; }
  .card .label { font-size: 13px; color: #9aa0a6; margin-bottom: 6px; }
  .card .value { font-size: 32px; font-weight: 600; }
  .card .unit { font-size: 16px; color: #9aa0a6; font-weight: 400; }
  .status-row { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 16px; }
  .pill { padding: 6px 14px; border-radius: 8px; font-size: 13px; font-weight: 500;
          background: #1a1d23; border: 1px solid #2a2e37; }
  .pill.charging { background: #1e2a3a; color: #7ab8f0; border-color: #2a4055; }
  .pill.fault    { background: #3a1e1e; color: #f08a8a; border-color: #552a2a; }
  .pill.full     { background: #2a2a1e; color: #e8d47a; border-color: #55502a; }
  .controls { display: flex; gap: 12px; margin-bottom: 16px; }
  .btn { flex: 1; padding: 16px; border-radius: 12px; border: none;
         font-size: 16px; font-weight: 600; cursor: pointer; color: #fff;
         transition: opacity .15s; }
  .btn:active { opacity: .7; }
  .btn:disabled { opacity: .35; cursor: not-allowed; }
  .btn.on  { background: #1f7a45; }
  .btn.off { background: #a33232; }
  .btn.mag { background: #6a4bb3; }
  .relay-badge { font-size: 14px; font-weight: 600; padding: 4px 12px; border-radius: 8px; }
  .relay-badge.on  { background: #1e3a2a; color: #6ee7a0; }
  .relay-badge.off { background: #2a2e37; color: #9aa0a6; }
  .event { background: #16181d; border: 1px solid #2a2e37; border-radius: 8px;
           padding: 10px 14px; font-size: 13px; color: #c8ccd2; margin-bottom: 12px;
           min-height: 40px; display: flex; align-items: center; }
  .event.warn { border-color: #552a2a; color: #f0a0a0; }
  .foot { color: #6a6f78; font-size: 12px; margin-top: 8px; line-height: 1.7; }
</style>
</head>
<body>
  <h1>NPB-1700 Charging Station</h1>
  <div class="sub">Remote monitor · <span id="conn" class="conn off">Connecting…</span>
      · CAN <span id="can" class="conn off">--</span>
      · Relay <span id="relay" class="relay-badge off">OFF</span></div>

  <div class="controls">
    <button id="btnOn"  class="btn on"  onclick="charge('on')">Start charging</button>
    <button id="btnOff" class="btn off" onclick="charge('off')">Stop charging</button>
  </div>

  <div class="controls">
    <button id="btnMag" class="btn mag" onclick="magnet()">Release magnet (5 s)</button>
  </div>

  <div class="event" id="event">—</div>

  <div class="grid">
    <div class="card"><div class="label">Voltage</div><div class="value"><span id="v">--</span> <span class="unit">V</span></div></div>
    <div class="card"><div class="label">Current</div><div class="value"><span id="i">--</span> <span class="unit">A</span></div></div>
    <div class="card"><div class="label">Charger temp</div><div class="value"><span id="t">--</span> <span class="unit">°C</span></div></div>
    <div class="card"><div class="label">Power</div><div class="value"><span id="p">--</span> <span class="unit">W</span></div></div>
  </div>

  <div class="status-row" id="pills"></div>
  <div class="foot">Charge time <span id="elapsed">00:00:00</span> · Faults <span id="faults">0</span>
      · Last update <span id="upd">--</span> · CAN packets <span id="rx">0</span><br>
      Charger curve setting: CV <span id="cv">--</span> V · CC <span id="cc">--</span> A</div>

<script>
  const $ = id => document.getElementById(id);

  function fmtTime(sec) {
    const h = String(Math.floor(sec/3600)).padStart(2,'0');
    const m = String(Math.floor((sec%3600)/60)).padStart(2,'0');
    const ss = String(sec%60).padStart(2,'0');
    return h+':'+m+':'+ss;
  }

  async function charge(action) {
    $("btnOn").disabled = true; $("btnOff").disabled = true;
    try {
      const r = await fetch("/charge/" + action, { method: "POST" });
      const d = await r.json();
      showEvent(d.message, !d.ok);
    } catch (e) {
      showEvent("Command failed to send", true);
    }
  }

  async function magnet() {
    $("btnMag").disabled = true;
    showEvent("Releasing magnet…", false);
    try {
      const r = await fetch("/magnet/release?ms=5000", { method: "POST" });
      const d = await r.json();
      showEvent(d.message, !d.ok);
    } catch (e) {
      showEvent("Command failed to send", true);
    }
  }

  function showEvent(msg, warn) {
    const el = $("event");
    el.textContent = msg || "—";
    el.className = "event" + (warn ? " warn" : "");
  }

  function render(d) {
    $("v").textContent = d.voltage.toFixed(2);
    $("i").textContent = d.current.toFixed(2);
    $("t").textContent = d.temp.toFixed(1);
    $("p").textContent = (d.voltage * d.current).toFixed(0);
    $("rx").textContent = d.can_rx;
    $("faults").textContent = d.faults;
    $("elapsed").textContent = fmtTime(d.elapsed);
    $("upd").textContent = new Date(d.ts * 1000).toLocaleTimeString();
    $("cv").textContent = d.curve_cv === null ? "--" : d.curve_cv.toFixed(2);
    $("cc").textContent = d.curve_cc === null ? "--" : d.curve_cc.toFixed(2);

    const can = $("can");
    can.textContent = d.can_ok ? "OK" : "No response";
    can.className = "conn " + (d.can_ok ? "ok" : "off");

    const mag = $("btnMag");
    mag.disabled = d.magnet_on || d.magnet_cooldown > 0 || !!d.magnet_lock;
    mag.title = d.magnet_lock || "";
    mag.textContent = d.magnet_on ? "Releasing magnet…"
                    : d.magnet_lock ? "⚠ Cannot release: " + d.magnet_lock
                    : d.magnet_cooldown > 0 ? "Cooling down " + d.magnet_cooldown.toFixed(0) + " s"
                    : "Release magnet (5 s)";

    const relay = $("relay");
    relay.textContent = d.charging ? "ON" : "OFF";
    relay.className = "relay-badge " + (d.charging ? "on" : "off");
    $("btnOn").disabled  = d.charging;
    $("btnOff").disabled = !d.charging;

    if (d.last_event) {
      showEvent(d.last_event, d.last_event.indexOf("⚠") >= 0 || d.last_event.indexOf("Cannot") >= 0);
    }

    const pills = [];
    d.chg_status.forEach(c => {
      let cls = "pill";
      if (c === "CC" || c === "CV" || c === "Float") cls += " charging";
      if (c === "FULL") cls += " full";
      if (c === "BTNC") cls += " fault";
      pills.push('<span class="' + cls + '">' + c + '</span>');
    });
    if (d.has_fault) {
      d.fault.forEach(f => pills.push('<span class="pill fault">' + f + '</span>'));
    }
    pills.push('<span class="pill">Battery ' + (d.bat_ok ? "full" : "not full") + '</span>');
    $("pills").innerHTML = pills.join("");
  }

  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(proto + "://" + location.host + "/ws");
    ws.onopen = () => { $("conn").textContent = "Connected"; $("conn").className = "conn ok"; };
    ws.onclose = () => {
      $("conn").textContent = "Disconnected, reconnecting…"; $("conn").className = "conn off";
      setTimeout(connect, 2000);
    };
    ws.onmessage = (e) => render(JSON.parse(e.data));
  }
  connect();
</script>
</body>
</html>"""

# ======================== 啟動 ========================
def hardware_init():
    global bus
    gpio_init()
    print("  GPIO OK")
    try:
        bus = can.interface.Bus(CAN_CHANNEL, interface='socketcan')
        print("  CAN  OK")
        threading.Thread(target=can_rx_thread, daemon=True).start()
    except Exception as e:
        print(f"  CAN  失敗: {e} (將拒絕開啟充電)")
        bus = None
    # 監控執行緒一定要啟動 (即使 CAN 失敗)
    threading.Thread(target=monitor_thread, daemon=True).start()

def local_addrs():
    try:
        return subprocess.run(['hostname', '-I'], capture_output=True, text=True, timeout=2).stdout.split()
    except Exception:
        return []

if __name__ == '__main__':
    print("\n  NPB-1700-48 充電站服務 v7 (監測 + 控制)")
    hardware_init()
    addrs = [a for a in local_addrs() if ':' not in a]
    print("  網頁: " + (" | ".join(f"http://{a}:8000" for a in addrs) or "http://<本機IP>:8000") + "\n")
    try:
        uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
    finally:
        if chip:
            try:
                lgpio.gpio_write(chip, PIN_RELAY, 0)
            except Exception:
                pass
            try:
                magnet_off()
            except Exception:
                pass
            s.charging = False
            release_outputs_safe()
            lgpio.gpiochip_close(chip)
        if bus: bus.shutdown()
        print("  再見\n")
