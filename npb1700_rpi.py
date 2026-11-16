#!/usr/bin/env python3
"""
NPB-1700-48 充電站監測 v4 (lgpio 版)
- 開關控制：GPIO 繼電器 (Pin 7/8 Remote ON/OFF)
- 監控：CAN Bus 讀取電壓/電流/溫度/故障
- 安全：CAN 故障旗標觸發繼電器斷電 + 實體急停按鈕

接線：
  RPi GPIO22 (Pin15) → Relay IN
  Relay COM/NO → NPB Pin 8
  NPB Pin 7 → 急停按鈕 NO → Relay COM  (串聯)
  即：NPB Pin7 → 急停 → Relay → NPB Pin8

啟動: sudo python3 npb1700_rpi_v4.py
"""

import lgpio
import can
import time
import threading

# ======================== GPIO ========================
PIN_BAT_OK = 17    # Pin 11 ← NPB Pin 5 (經分壓)
PIN_CHG_OK = 27    # Pin 13 ← NPB Pin 6 (經分壓)
PIN_RELAY  = 22    # Pin 15 → Relay IN

# ======================== CAN ========================
DEVICE_ADDR = 3
TX_ID = 0x000C0100 | DEVICE_ADDR
RX_ID = 0x000C0000 | DEVICE_ADDR

CMD = {
    'FAULT_STATUS': 0x0040,
    'READ_VOUT':    0x0060,
    'READ_IOUT':    0x0061,
    'READ_TEMP':    0x0062,
    'CURVE_CONFIG': 0x00B4,
    'CHG_STATUS':   0x00B8,
}

# ======================== 安全閾值 ========================
VOLTAGE_MAX  = 58.0   # 過壓保護 (V)
CURRENT_MAX  = 40.0   # 過流保護 (A)
TEMP_MAX     = 85.0   # 過溫保護 (°C)
POLL_INTERVAL = 3.0   # 充電中自動輪詢間隔 (秒)
SAFETY_INTERVAL = 1.0 # 安全檢查間隔 (秒)

# ======================== 狀態 ========================
class State:
    voltage = 0.0; current = 0.0; temp = 0.0
    fault = 0; chg_st = 0
    bat_ok = False; chg_ok = False
    charging = False; can_ok = False
    can_rx = 0; faults = 0
    t_start = None; t_total = 0

s = State()
bus = None
chip = None
lock = threading.Lock()

# ======================== CAN ========================
def can_read(cmd_code):
    """送出 CAN 讀取指令 (2 bytes: cmd_lo, cmd_hi)"""
    msg = can.Message(arbitration_id=TX_ID, is_extended_id=True,
        data=[cmd_code & 0xFF, (cmd_code >> 8) & 0xFF])
    try:
        bus.send(msg)
        return True
    except Exception as e:
        return False

def parse_reply(msg):
    """解析充電器的 CAN 回覆"""
    if msg.arbitration_id != RX_ID or msg.dlc < 2:
        return
    cmd = msg.data[0] | (msg.data[1] << 8)
    val = 0
    if msg.dlc >= 4:
        val = msg.data[2] | (msg.data[3] << 8)
    elif msg.dlc >= 3:
        val = msg.data[2]

    with lock:
        s.can_rx += 1
        s.can_ok = True
        if   cmd == CMD['READ_VOUT']:    s.voltage = val * 0.01
        elif cmd == CMD['READ_IOUT']:    s.current = val * 0.01
        elif cmd == CMD['READ_TEMP']:    s.temp = val * 0.1
        elif cmd == CMD['FAULT_STATUS']: s.fault = val
        elif cmd == CMD['CHG_STATUS']:   s.chg_st = val

def can_rx_thread():
    """背景接收 CAN 回覆"""
    while True:
        try:
            msg = bus.recv(timeout=1.0)
            if msg:
                parse_reply(msg)
        except:
            pass

def poll_all():
    """輪詢所有參數"""
    for cmd in ['READ_VOUT', 'READ_IOUT', 'READ_TEMP', 'FAULT_STATUS', 'CHG_STATUS']:
        can_read(CMD[cmd])
        time.sleep(0.03)

# ======================== GPIO ========================
def gpio_init():
    global chip
    chip = lgpio.gpiochip_open(0)
    lgpio.gpio_claim_input(chip, PIN_BAT_OK)
    lgpio.gpio_claim_input(chip, PIN_CHG_OK)
    lgpio.gpio_claim_output(chip, PIN_RELAY, 0)

def read_gpio():
    s.bat_ok = bool(lgpio.gpio_read(chip, PIN_BAT_OK))
    s.chg_ok = bool(lgpio.gpio_read(chip, PIN_CHG_OK))

# ======================== 控制 ========================
def charger_on():
    if s.charging:
        print("  已在充電中")
        return
    # 開啟前先輪詢一次確認沒有故障
    if bus:
        can_read(CMD['FAULT_STATUS'])
        time.sleep(0.1)
        fault_bits = s.fault & ~0x40  # 忽略 OP_OFF (因為還沒開)
        if fault_bits:
            print(f"  ✗ 無法啟動，充電器有故障: {fmt_fault(fault_bits)}")
            return
    lgpio.gpio_write(chip, PIN_RELAY, 1)
    s.charging = True
    s.t_start = time.time()
    print("  ✓ 充電器 ON (繼電器)")

def charger_off():
    lgpio.gpio_write(chip, PIN_RELAY, 0)
    if s.charging and s.t_start:
        s.t_total += int(time.time() - s.t_start)
    s.charging = False
    s.t_start = None
    print("  ✓ 充電器 OFF (繼電器)")

def emergency_off(reason):
    s.faults += 1
    print(f"\n  ⚠ 緊急斷電: {reason} (#{s.faults})")
    charger_off()

def safety_check():
    """基於 CAN 故障旗標 + 軟體閾值的安全檢查"""
    read_gpio()

    # GPIO: 電池充滿信號
    if s.bat_ok and s.charging:
        print("  ● 電池充滿信號，停止充電")
        charger_off()
        return

    # 以下只在充電中檢查
    if not s.charging:
        return

    # CAN 故障旗標
    if s.fault & 0x02: emergency_off("OTP 過溫 (CAN)")
    if s.fault & 0x04: emergency_off("OVP 過壓 (CAN)")
    if s.fault & 0x10: emergency_off("SHORT 短路 (CAN)")
    if s.fault & 0x20: emergency_off("AC_FAIL 交流異常 (CAN)")

    # 軟體閾值保護
    if s.voltage > VOLTAGE_MAX: emergency_off(f"電壓過高 {s.voltage:.1f}V > {VOLTAGE_MAX}V")
    if s.current > CURRENT_MAX: emergency_off(f"電流過大 {s.current:.1f}A > {CURRENT_MAX}A")
    if s.temp > TEMP_MAX:       emergency_off(f"溫度過高 {s.temp:.1f}°C > {TEMP_MAX}°C")

# ======================== 顯示 ========================
def fmt_fault(f):
    fl = []
    if f & 0x80: fl.append("HI_TEMP")
    if f & 0x40: fl.append("OP_OFF")
    if f & 0x20: fl.append("AC_FAIL")
    if f & 0x10: fl.append("SHORT")
    if f & 0x08: fl.append("OLP")
    if f & 0x04: fl.append("OVP")
    if f & 0x02: fl.append("OTP")
    return ', '.join(fl) if fl else "None"

def fmt_chg(c):
    hi = (c >> 8) & 0xFF
    lo = c & 0xFF
    parts = []
    if lo & 0x01: parts.append("FULL")
    if lo & 0x02: parts.append("CC")
    if lo & 0x04: parts.append("CV")
    if lo & 0x08: parts.append("Float")
    if hi & 0x08: parts.append("BTNC")  # Battery Not Connected
    return ', '.join(parts) if parts else "Idle"

def show():
    read_gpio()
    sec = s.t_total + (int(time.time() - s.t_start) if s.charging and s.t_start else 0)
    G = "\033[32m"; Y = "\033[33m"; R = "\033[31m"; C = "\033[36m"; W = "\033[0m"

    relay_str = f"{G}ON{W}" if s.charging else "OFF"
    can_str = f"{G}OK{W} ({s.can_rx})" if s.can_ok else f"{Y}等待{W}"
    bat_str = f"{Y}FULL{W}" if s.bat_ok else f"{C}Charging{W}"
    fault_str = fmt_fault(s.fault)
    if s.fault & ~0x40:  # 有真正的故障 (忽略 OP_OFF)
        fault_str = f"{R}{fault_str}{W}"

    print(f"""
  ┌─── NPB-1700-48 v4 ───────────────────┐
  │ Battery    : {bat_str:30s}   │
  │ Relay      : {relay_str:30s}   │
  │ CAN        : {can_str:30s}   │
  │                                       │
  │ 電壓  {s.voltage:7.2f} V    電流  {s.current:6.2f} A    │
  │ 溫度  {s.temp:7.1f} °C   充電  {fmt_chg(s.chg_st):10s}  │
  │ 故障  {fault_str:33s}│
  │ 累計  {sec//3600:02d}:{(sec%3600)//60:02d}:{sec%60:02d}         異常  {s.faults} 次     │
  └───────────────────────────────────────┘""")

def show_help():
    print("""
  ┌─── 指令 ─────────────────┐
  │  1   充電器 ON  (繼電器) │
  │  0   充電器 OFF (繼電器) │
  │  s   顯示狀態            │
  │  p   CAN 輪詢全部        │
  │  v   讀電壓              │
  │  i   讀電流              │
  │  t   讀溫度              │
  │  f   讀故障              │
  │  c   讀充電狀態          │
  │  r   重置計數            │
  │  h   說明                │
  │  q   離開 (自動關機)     │
  └──────────────────────────┘""")

# ======================== main ========================
def main():
    global bus

    print("\n  NPB-1700-48 充電站 v4")
    print("  控制: GPIO 繼電器 | 監控: CAN Bus")
    print(f"  TX 0x{TX_ID:08X} | RX 0x{RX_ID:08X}\n")

    gpio_init()
    print("  GPIO OK")

    try:
        bus = can.interface.Bus('can0', interface='socketcan')
        print("  CAN  OK")
        threading.Thread(target=can_rx_thread, daemon=True).start()
    except Exception as e:
        print(f"  CAN  失敗: {e} (僅繼電器控制，無監控)")
        bus = None

    show_help()
    if bus:
        poll_all()
        time.sleep(0.2)
    show()

    t_safe = time.time()
    t_poll = time.time()

    try:
        while True:
            cmd = input().strip().lower()

            if   cmd == '1': charger_on()
            elif cmd == '0': charger_off()
            elif cmd == 's':
                if bus: poll_all(); time.sleep(0.15)
                show()
            elif cmd == 'p':
                if bus:
                    print("  查詢中...")
                    poll_all()
                    time.sleep(0.15)
                    show()
                else:
                    print("  CAN 未連接")
            elif cmd == 'v':
                if bus: can_read(CMD['READ_VOUT']); time.sleep(0.1); print(f"  電壓: {s.voltage:.2f} V")
            elif cmd == 'i':
                if bus: can_read(CMD['READ_IOUT']); time.sleep(0.1); print(f"  電流: {s.current:.2f} A")
            elif cmd == 't':
                if bus: can_read(CMD['READ_TEMP']); time.sleep(0.1); print(f"  溫度: {s.temp:.1f} °C")
            elif cmd == 'f':
                if bus: can_read(CMD['FAULT_STATUS']); time.sleep(0.1); print(f"  故障: {fmt_fault(s.fault)}")
            elif cmd == 'c':
                if bus: can_read(CMD['CHG_STATUS']); time.sleep(0.1); print(f"  充電: {fmt_chg(s.chg_st)}")
            elif cmd == 'r':
                s.faults = 0; s.t_total = 0; s.can_rx = 0
                print("  已重置")
            elif cmd == 'h': show_help()
            elif cmd == 'q': charger_off(); break
            elif cmd == '': pass
            else: print(f"  未知指令: {cmd}")

            # 定期安全檢查
            now = time.time()
            if now - t_safe >= SAFETY_INTERVAL:
                t_safe = now
                if bus and s.charging:
                    poll_all()
                    time.sleep(0.1)
                safety_check()

            # 充電中自動輪詢 (非充電時不輪詢，省 CAN 流量)
            if bus and s.charging and now - t_poll >= POLL_INTERVAL:
                t_poll = now
                poll_all()

    except KeyboardInterrupt:
        print("\n  Ctrl+C 偵測到")
        charger_off()
    finally:
        if chip:
            lgpio.gpiochip_close(chip)
        if bus:
            bus.shutdown()
        print("  再見\n")

if __name__ == '__main__':
    main()
