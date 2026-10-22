# 可執行 SDK 範例與現行介面

這些範例對應本候選版本，使用同一 checkout 建出的 Python／Rust wheels；本輪沒有發布
PyPI 套件，不能假定已發布的同版本號套件具有全部新增 API。安裝驗證見
[支援矩陣](installation-matrix.md)，完整契約見 [Library API](library-api.md)。

## 七個整合案例

在 checkout 的專案環境執行：

```bash
.venv/bin/python -m examples.sdk_offline
.venv/bin/python -m examples.sdk_offline --case processor
```

每個案例有實際輸出斷言；錯誤會使程序失敗，不只列印成功訊息。

| `--case` | 交付內容 | 驗證行為 |
| --- | --- | --- |
| `numpy` | 預檢、mapping 建構、NumPy command／詳細結果 | 超界輸出 CLAMP、型別／值符合預期，狀態是 returned |
| `torch` | CPU Tensor、requires-grad 與 dtype/device | 修正值正確，輸出保留 device/dtype 並離開訓練圖 |
| `mobile-base` | `base_pose` 觀測與 `[v, omega]` 速度命令 | 明列 m/s、rad/s；違規回傳明確選定的零速度 |
| `arm` | 六軸 preset、radian 命令、打包 URDF／FK | 規則修正與實際 FK 執行；示範限制不代表實機校準 |
| `processor` | LeRobot transition、首輪 hold、後續 clamp、reset | 只使用公開入口；結束時 close |
| `recording` | 臨時 MCAP、兩個控制週期、立即讀回 | close 後 footer 與兩筆 cycle 完整可讀，臨時檔清除 |
| `recovery` | 依賴故障、FAULT／fallback、修復、reset | 故障不說成 PASS；恢復後 PASS；缺少 action 仍拒絕 |

同一檔案也能以安裝環境的 `python -I /absolute/path/examples/sdk_offline.py` 執行，
不需要把 checkout 加入 Python 搜尋路徑。

## 其他離線範例與清理

- `guardrail_evaluation.py`、`guardrail_preflight.py`、`guardrail_session.py`：詳細結果、
  無資源預檢、pending／applied 回報與 reset。
- `hello_guard.py`、`custom_callback.py`：公開 guard／callback 擴充；PASS、CLAMP 或 REJECT
  都有斷言。需要回報修正時使用公開 `dam.guard.pipeline.CallbackResult.clamp`，
  不把 action 原地改寫後仍回傳 True 當成 CLAMP 證據。
- `mobile_base_guardrail.py`、`jetbot_guardrail.py`：帶預測器的底盤命令範例。
  Jetbot 有選用 QP 相依時回報 CLAMP；未安裝時拒絕並回零速度。
- `safe_record.py`：one-shot、長期 session、processor 三種層級，錄製至可丟棄目錄。
- `isaac_franka_demo.py`：純 Tensor 與批次輸入的離線檢查，沒有啟動 Isaac Sim，沒有
  宣稱無 CPU conversion 或 1 kHz 效能；超界以實際 decision 顯示，不把所有變動叫 CLAMP。
- `safety_comparison_demo.py`：使用公開 Safety 服務介面的離線 A/B／regression 範例。
  CI 不傳 `--install-for-console`，不修改使用者 library 或錄製資料。

`scripts/acceptance/examples_smoke.py` 逐一以安裝環境、`-I`、checkout 外的臨時工作目錄
執行上述 **11 個離線程式**，保存退出碼、stdout 與 stderr。新增範例必須明確分類，
未審閱的檔案使驗證失敗。所有 SDK session 都透過 context manager 或 finally 清理。

`isaac_jetbot_lane_demo.py` 需要外部 Isaac Lab／場景與專案依賴，沒有在這個離線矩陣執行；
不把它列為通過或宣稱模擬器／實機部署完成。

## 現行介面與歷史寫法

這裡說明辨識舊文件的方法，不提供相容 shim；歷史交接的舊名稱不是現行 API 保證。

| 歷史或容易誤用的寫法 | 現行做法 |
| --- | --- |
| `SafetyGuard(action, obs)`、分開傳兩個位置參數 | `dam.Guardrail(config)`，每輪傳 `{"joints": obs, "action": action}` 等觀測群組 |
| 從私有 `_runtime`／`_guard` 讀取結果或切換狀態 | `evaluate`、`diagnostics`、`session_status`、`request_reload`／`reload_receipt`、`reset` |
| 收到重載 accepted 就說已套用 | pending 只代表排隊；等週期 receipt 是 applied，核對 config_version |
| 所有 command-space 都預設 hold 或補零 | 依命令語意明確提供 `safe_action`；非 joint 的預設 hold 拒絕建構 |
| 以 preset 名稱推斷 degree／radian | 在 hardware／motor interface 或 constructor 明列 degrees_mode；preset 不定義單位 |
| 每輪重新建構 one-shot 以處理連續控制 | 重用 session 保留歷史，在 teardown 關閉；one-shot 用於單筆離線用途 |
| 結果 returned 就等於已送到設備 | SDK 沒有 sink；returned 僅代表命令回到呼叫端，設備送出另需實際回報 |
| 關閉發生例外後仍繼續使用原 session | 修正原因，重試 close／reset；部分清理後的 recovery_required 阻擋評估 |

`Guardrail` 不支援同一 instance 上並行 evaluate／reset／close。慢速 callback 的等待逾時
不會強制取消 Python 執行；關閉必須確認 worker 停止。錄製入列不是持久化完成；成功 close
代表 worker 與 footer 完成，沒有承諾斷電耐久性。
