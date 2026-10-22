# L5／R09 診斷、效能與長時間穩定性

日期：2026-10-22。狀態：進行中，尚未通過 8 小時門檻。基準為 L4 `1c48e89` 加本階段差異。

## 已修正與驗證

- 公開 `Guardrail.diagnostics()` 提供 session／版本、每層與每個 guard 延遲、完整呼叫時間、
  慢速判定年齡、拒絕／修正率、程序資源與錄製錯誤快照；回傳資料與內部狀態分離。
- 重現錄製背景開檔失敗未拋錯、close 返回後檔案尚未完成：新增 3 個案例於修正前失敗。
  Rust worker 現在保留錯誤，close 有有界等待、完成 footer 並 join；逾時保留 worker 供重試。
  Python 不再吞掉錄製／關閉／佇列溢位錯誤，公開 SDK 保留分類與 cause；新 session 可恢復。
- 診斷、callback timeout、solver fault、磁碟寫入失敗、recording close timeout 與恢復均有測試。
  全套非硬體 Python **1333 passed in 25.63s**。Rust workspace 測試通過；既有忽略項目不算通過。
- 本輪修正後使用明確的 `.venv` interpreter 安裝 Rust wheel，避免 shell 的 Conda 設定選錯環境。

Processor 摘要亦修正只有 FAULT 卻顯示全部通過，以及以 guard 事件數計算週期比例造成超過 100%
的錯誤；補上 fault-only／多 guard 同輪回歸。正式長測尚未開始前已修正。

## 固定量測方式與待驗收項目

使用 `python -m scripts.acceptance.runtime_stability`，模式為 `benchmark`、`lifecycle`、`soak`，
必須指定 `--output`。固定 50 Hz、p99 ≤ 10 ms、整輪 20 ms 超期率 ≤ 0.1%，沒有調寬參數。
`--probe` 只檢查工具能運作，報告一定標示 incomplete，不能代替驗收。

- benchmark：6 軸 joint position，固定 PASS／CLAMP 輸入；同步 fast 與 10 Hz slow worker hybrid
  各 10,000 cycles。100 cycles 暖身，初始化與首次等待另列。超期從排定週期起點算到結果返回，
  包含排程及採樣延誤；完整 evaluate 時間作為快路徑與交接開銷的保守上界。
- lifecycle：1,000 次建立、evaluate、reset、evaluate、close／重複 close，包含實際 MCAP；
  每次必須完成兩個錄製檔，隨後移除臨時資料。檢查 worker、OS threads、檔案描述符。
- soak：hybrid 固定負載 1,440,000 cycles，實際至少 8 小時；每分鐘同條件採 RSS／資源樣本，
  比較 8 個小時的中位數與描述符。這個 soak 不啟用錄製；錄製清理由 lifecycle 與故障測試驗證。
  原始延遲以 binary triples 寫入磁碟，不把 8 小時樣本全部累積在 Python 記憶體。
- 報告保留平台、相依、設定與來源 SHA-256；延遲分析陣列在資源採樣結束後才載入。
  未完成實際長測以前不得宣布 L5 或整體計畫完成，也不宣稱硬即時或實機驗證。

測量主機為 Apple M4／macOS arm64／Python 3.12。關閉部分失敗後 session 進入 recovery_required，
不允許使用已部分停止的 runtime 繼續評估。

工具 probes 已執行並如預期標為 incomplete。正式 benchmark／lifecycle／soak 結果待補。
原始紀錄保存於 `logs/acceptance-l5*`。
