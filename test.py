#!/usr/bin/env python3
"""
NPB-1700-48 充電站監測 v4 (純 CAN 控制 + 急停偵測版)
"""

import lgpio
import can
import time
import threading
import sys

# ======================== GPIO ========================
# 假設你有一根 GPIO 專門用來讀取急停按鈕的實體狀態 (例如透過急停按鈕的雙軌接點)
# 如果沒有實體腳位讀取，程式也會自動從 CAN Bus 的 FAULT_STATUS 判斷
PIN_ESTOP_MONITOR = 17  # 請依實體接線調整，接急停按鈕的輔助訊號線
chip = None

# ======================== CAN 暫存器與位址 ========================
DEVICE_ADDR = 3
TX_ID = 0x000C0100 | DEVICE_ADDR
RX_ID = 0x000C0000 | DEVICE_ADDR

CMD = {
    'OPERATION':    0x0000,
    'FAULT_STATUS': 0x0040,
    'READ_VOUT':    0x0060,
    'READ_IOUT':    0x0061,
    'READ_TEMP':    0x0062,
    'CHG_STATUS':   0x00B8,
}

class State:
    voltage = 0.0; current = 0.0; temp = 0.0
    fault = 0; chg_st = 0
    charging = False; can_ok = False
    estop_active = False  # 急停按鈕狀態：True 代表被壓下(斷開)

s = State()
bus = None
lock = threading.Lock()

def can_write(cmd_code, value):
    msg = can.Message(arbitration_id=TX_ID, is_extended_id=True,
                      data=[cmd_code & 0xFF, (cmd_code>>8) & 0xFF,
                            value & 0xFF, (value>>8) & 0xFF])
    try: bus.send(msg); return True
    except: return False

def can_read(cmd_code):
    msg = can.Message(arbitration_id=TX_ID, is_extended_id=True,
                      data=[cmd_code & 0xFF, (cmd_code>>8) & 0xFF])
    try: bus.send(msg); return True
    except: return False

def parse_reply(msg):
    if msg.dlc < 2: return
    cmd = msg.data[0] | (msg.data[1] << 8)
    val = 0
    if msg.dlc >= 4: val = msg.data[2] | (msg.data[3] << 8)
    
    with lock:
        s.can_ok = True
        if   cmd == CMD['READ_VOUT']:    s.voltage = val * 0.01
        elif cmd == CMD['READ_IOUT']:    s.current = val * 0.01
        elif cmd == CMD['FAULT_STATUS']: 
            s.fault = val
            # 明緯底層：當 FAULT_STATUS 的 Bit 6 (0x40) 為 1 時，代表 Remote ON/OFF 斷開（急停觸發）
            old_estop = s.estop_active
            s.estop_active = bool(val & 0x40)
            if s.estop_active and not old_estop:
                print("\n\n\033[31m[警報] 偵測到實體急停按鈕被壓下！充電器硬體已斷開！\033[0m\n")
                s.charging = False
            elif not s.estop_active and old_estop:
                print("\n\n\033[32m[通知] 實體急停按鈕已復位，恢復通訊準備狀態。\033[0m\n")
                # 復位瞬間立刻補發 OFF 指令，防止充電器直接噴大電流
                can_write(CMD['OPERATION'], 0x00) 

def can_rx_thread():
    while True:
        try:
            msg = bus.recv(timeout=0.5)
            if msg: parse_reply(msg)
        except: pass

# ======================== 控制邏輯 (含硬體連鎖) ========================
def charger_on():
    if s.estop_active:
        print("\n\033[31m[拒絕執行] 實體急停已壓下（Pin 7/8 斷開），CAN 指令無效！\033[0m")
        return
    if bus:
        if can_write(CMD['OPERATION'], 0x01):
            s.charging = True
            print("   ✓ CAN 指令：開啟充電 (OPERATION = 0x01)")
        else:
            print("   ✗ CAN 指令發送失敗")

def charger_off():
    # 無論急停有沒有壓下，切斷指令都可以發送（做為保險）
    if bus:
        if can_write(CMD['OPERATION'], 0x00):
            s.charging = False
            print("   ✓ CAN 指令：關閉充電 (OPERATION = 0x00)")

def poll_safety():
    """定期輪詢故障暫存器以更新急停狀態"""
    if bus:
        can_read(CMD['FAULT_STATUS'])

# ======================== 主程式 ========================
def main():
    global bus, chip
    print("\n   NPB-1700 純 CAN 穩定控制系統 v4")
    
    # 初始化 SocketCAN
    try:
        bus = can.interface.Bus('can0', interface='socketcan')
        threading.Thread(target=can_rx_thread, daemon=True).start()
        print("   ✓ CAN0 介面初始化成功")
    except Exception as e:
        print(f"   ✗ CAN0 初始化失敗: {e}")
        sys.exit(1)

    # 剛啟動時，不管三七二十一，先連續發送兩次 OFF 指令，壓制住常閉的 Pin 7/8
    print("   正在接管充電器控制權...")
    can_write(CMD['OPERATION'], 0x00)
    time.sleep(0.1)
    can_write(CMD['OPERATION'], 0x00)

    # 主迴圈
    t_poll = time.time()
    try:
        while True:
            # 這裡使用非阻塞或有超時的輸入，此處簡化為背景定期輪詢
            # 為了能一邊輸入一邊背景輪詢，實際運作時，安全檢查放在 input 之後或獨立執行緒
            print("\n輸入指令 [1:ON, 0:OFF, q:離開]: ", end="", flush=True)
            cmd = input().strip().lower()
            
            # 執行安全輪詢更新狀態
            poll_safety()
            time.sleep(0.05) # 等待 CAN 接收執行緒更新 State

            if cmd == '1':
                charger_on()
            elif cmd == '0':
                charger_off()
            elif cmd == 'q':
                charger_off()
                break
            
            # 背景監控：若在等待輸入時，急停觸發，會在 parse_reply 中直接噴出提示

    except KeyboardInterrupt:
        charger_off()
    finally:
        if bus: bus.shutdown()
        print("   系統關閉\n")

if __name__ == '__main__':
    main()
