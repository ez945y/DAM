# S2／R04 最終輸出與 fallback 驗收

狀態：S2 行為驗收完成，[S3](acceptance-s3.md) 全套候選版檢查通過。基準為 `b238cff` 加本工作目錄
未提交差異；平台為 macOS arm64、Python 3.12。

## 輸出契約與路徑

| 路徑 | 最終檢查／失敗處理 |
| --- | --- |
| Runtime 原始／修正命令 | ExecutionEngine 在 guard 前檢查 proposal、後檢查結果；step 在 sink 前再次檢查 |
| MONITOR／LOG_ONLY | 可保留有限的限制違反命令，但不豁免格式、有限值或維度檢查 |
| SDK | 輸入與 fallback 長度／有限值檢查；使用同一 runtime 驗證；Tensor 轉回原 dtype 後檢查溢位 |
| HoldPosition | 僅適用關節位置 layout；無歷史時持續抑制；有歷史仍須通過最終檢查 |
| Retreat | 必須有歷史及明確、同形狀、有限目標；每個 waypoint 通過 guard 與最終檢查；無效前提形成 fallback FAULT |
| SlowDown | 僅適用關節位置 layout；無歷史時抑制；guard 後處理仍受最終檢查；保留時間戳與夾爪欄位 |
| 自訂 context | 即使 bypass guard，輸出也必須通過最終檢查 |
| EmergencyStop／WaitAndRetry | 不送出動作；停止 hook 失敗保留原因與終止狀態，不能假稱硬體已停止 |
| Sink | 方法正常返回才標記 sent 並更新命令歷史；拋錯為 failed，保留先前歷史 |

位置命令為非空一維有限向量，大小優先由 action layout 決定，其次為宣告的 joint names。
沒有宣告維度時，直接驗證以輸入形狀固定該次輸出大小；此情況不能證明設備相容性。
選用速度須與位置同形狀且有限；夾爪為有限純量 `[0, 1]`；時間戳為非負有限純量，
預設 `0` 表示未提供生成時間。None 表示沒有選用速度／夾爪命令。
EE proposal 若附帶 pose，必須為 7 個有限值；本檢查不聲稱姿態可達或幾何安全。

Runtime 關節量採 radians／radians per second；SDK 在關節命令邊界依設定轉換 degree。
非關節 layout 保留呼叫端的宣告語意，不推定單位；SDK 的預設 hold 不再轉成隱含全零命令，
須明確提供 fallback 向量或 `zero`。明確提供仍不代表該命令對設備安全。

## 缺陷與修正

- 重現三種 enforcement mode 都能將 NaN 送入 fake sink；已加入共用有效性檢查。
- sink 失敗原先仍可能留下已接受命令歷史；改成成功返回後才提交。
- context／policy／fallback／sink 例外保留來源與原因；ENFORCE 執行故障進入終止抑制。
- 同週期 fallback 再 step 可能重跑 callback；改為當下執行 entry，下週期才 step。
- Retreat 缺起點原先可返回 normal、目標維度不符原先改成零；改為明確故障。
- Hold／Retreat／SlowDown 不允許把非關節命令當成位置重播／插值。
- SlowDown 缺少歷史時原先送出未縮放命令，改成抑制；scale 必須有限且介於 0 與 1。

## 驗證紀錄

最終命令：`.venv/bin/python -m pytest tests/unit tests/integration tests/safety tests/property -q -m 'not hardware and not ros2'`。
原始紀錄為 `logs/acceptance-s2-python-final.log`；最終退出碼 0，
**1276 passed in 71.08s**。程式碼指紋保留於 `logs/acceptance-candidate-source.json`。
完整範圍涵蓋 unit／integration／safety／property，排除硬體與 ROS2。

先前故障注入確認 NaN 三模式失敗後完成修正；中途完整回歸不能作為最終證據：
測試程序載入舊版程式、測試檔卻已更新，造成 3 個方法不存在錯誤，另有 Watchdog
60 ms deadline 在高主機負載下失敗。後續固定程式碼再依序重跑；不提高 deadline。
SDK 的三個 EE layout 測試已改用明確指定的 pose fallback，原有輸出斷言保持不變。

Rust workspace 測試本輪退出碼 0；完整靜態檢查、前端與正式建置結果見 S3 報告。
所有驗證均為離線 fake sink，沒有實機停止或運動驗證。
