# NPB-1700-48 CAN Bus 指令參考表

## 基本設定

- 協議: CAN 2.0B, Extended 29-bit ID
- 鮑率: 250 Kbps
- 你的設備地址: **Device 3** (A0=open, A1=open)
- 發送 CAN ID: **0x000C0103** (Controller → Charger)
- 接收 CAN ID: **0x000C0003** (Charger → Controller)
- 最小發送間隔: 20ms
- 充電器回應時間: ≤5ms

## 資料格式

寫入 (DLC=4): [CMD_Lo, CMD_Hi, DATA_Lo, DATA_Hi]
讀取請求 (DLC=2): [CMD_Lo, CMD_Hi]
回覆 (DLC=2+N): [CMD_Lo, CMD_Hi, DATA_Lo, DATA_Hi, ...]

## 常用指令

### 讀取類 (監測用)

| 指令碼 | 名稱 | 說明 | 因子 | 範例回覆 |
|--------|------|------|------|---------|
| 0x0060 | READ_VOUT | 輸出電壓 | ×0.01V | 回傳 0x1270 = 4720 → 47.20V |
| 0x0061 | READ_IOUT | 輸出電流 | ×0.01A | 回傳 0x09C4 = 2500 → 25.00A |
| 0x0062 | READ_TEMP | 內部溫度 | ×0.1°C | 回傳 0x01C2 = 450 → 45.0°C |
| 0x0040 | FAULT_STATUS | 故障狀態 | 位元旗標 | 見下方位元定義 |
| 0x00B8 | CHG_STATUS | 充電狀態 | 位元旗標 | 見下方位元定義 |
| 0x00C1 | SYSTEM_STATUS | 系統狀態 | 位元旗標 | 見下方位元定義 |
| 0x0000 | OPERATION | 開關狀態 | - | 0x01=ON, 0x00=OFF |

### 寫入類 (控制用)

| 指令碼 | 名稱 | 說明 | 因子 | 範例 |
|--------|------|------|------|------|
| 0x0000 | OPERATION | 開/關機 | - | 寫 0x01=ON, 0x00=OFF |
| 0x0020 | VOUT_SET | 設定輸出電壓 | ×0.01V | 寫 6000 = 60.00V |
| 0x0030 | IOUT_SET | 設定輸出電流 | ×0.01A | 寫 500 = 5.00A |
| 0x00B0 | CURVE_CC | 充電CC電流 | ×0.01A | 寫 2500 = 25.00A |
| 0x00B1 | CURVE_CV | 充電CV電壓 | ×0.01V | 寫 5840 = 58.40V |

## FAULT_STATUS (0x0040) 位元定義

Low byte:
- Bit 7: HI_TEMP (1=內部高溫異常)
- Bit 6: OP_OFF (1=輸出被關閉)
- Bit 5: AC_FAIL (1=AC 輸入異常)
- Bit 4: SHORT (1=輸出短路)
- Bit 3: OLP (1=輸出過載)
- Bit 2: OVP (1=輸出過壓)
- Bit 1: OTP (1=過溫保護)

## CHG_STATUS (0x00B8) 位元定義

Low byte:
- Bit 0: FULLM (1=充滿)
- Bit 1: CCM (1=CC 恆流模式中)
- Bit 2: CVM (1=CV 恆壓模式中)
- Bit 3: FVM (1=浮充模式中)

High byte:
- Bit 3: BTNC (1=未偵測到電池)
- Bit 5: CCTOF (1=CC 階段逾時)
- Bit 6: CVTOF (1=CV 階段逾時)
- Bit 7: FVTOF (1=浮充階段逾時)

## SYSTEM_STATUS (0x00C1) 位元定義

Low byte:
- Bit 1: DC_OK (0=DC 輸出過低, 1=DC 正常)
- Bit 5: INITIAL_STATE (1=初始化中)
- Bit 6: EEPER (1=EEPROM 錯誤)

## 發送範例

讀取輸出電壓:
  送: ID=0x0C0103 DLC=2 Data=[0x60, 0x00]
  收: ID=0x0C0003 DLC=4 Data=[0x60, 0x00, Lo, Hi]
  電壓 = (Hi<<8 | Lo) × 0.01

設定輸出電壓 60V (6000 = 0x1770):
  送: ID=0x0C0103 DLC=4 Data=[0x20, 0x00, 0x70, 0x17]

設定輸出電流 5A (500 = 0x01F4):
  送: ID=0x0C0103 DLC=4 Data=[0x30, 0x00, 0xF4, 0x01]

CAN 開機:
  送: ID=0x0C0103 DLC=4 Data=[0x00, 0x00, 0x01, 0x00]

CAN 關機:
  送: ID=0x0C0103 DLC=4 Data=[0x00, 0x00, 0x00, 0x00]
