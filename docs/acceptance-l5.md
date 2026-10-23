# L5／R09 診斷、效能與長時間穩定性

日期：2026-10-22。狀態：2026-10-23 依使用者縮短範圍交付；8 小時門檻延後，未通過驗證。基準為 L4 `1c48e89` 加本階段差異。

## 已修正與驗證

- 公開 `Guardrail.diagnostics()` 提供 session／版本、每層與每個 guard 延遲、完整呼叫時間、
  慢速判定年齡、拒絕／修正率、程序資源與錄製錯誤快照；回傳資料與內部狀態分離。
- 重現錄製背景開檔失敗未拋錯、close 返回後檔案尚未完成：新增 3 個案例於修正前失敗。
  Rust worker 現在保留錯誤，close 有有界等待、完成 footer 並 join；逾時保留 worker 供重試。
  Python 不再吞掉錄製／關閉／佇列溢位錯誤，公開 SDK 保留分類與 cause；新 session 可恢復。
- 診斷、callback timeout、solver fault、磁碟寫入失敗、recording close timeout 與恢復均有測試。
  全套非硬體 Python **1337 passed in 25.31s**。Rust workspace 測試通過；既有忽略項目不算通過。
- 本輪修正後使用明確的 `.venv` interpreter 安裝 Rust wheel，避免 shell 的 Conda 設定選錯環境。

Processor 摘要亦修正只有 FAULT 卻顯示全部通過，以及以 guard 事件數計算週期比例造成超過 100%
的錯誤；補上 fault-only／多 guard 同輪回歸。正式長測尚未開始前已修正。

## 固定量測方式與待驗收項目

使用 `python -m scripts.acceptance.runtime_stability`，模式為 `benchmark`、`lifecycle`、`soak`，
每次自動在本 checkout 的 `logs/acceptance-l5/` 建立唯一目錄並印出位置。
CLI 不接受輸出路徑，根目錄拒絕符號連結逃逸，也不覆寫既有結果。固定 50 Hz、p99 ≤ 10 ms、整輪 20 ms 超期率 ≤ 0.1%，沒有調寬參數。
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

工具 probes 已執行並如預期標為 incomplete。第一輪 benchmark 在外部檢查發現輸出目錄未限縮後主動中斷，未進入 lifecycle／8 小時階段；
紀錄保留於 `logs/acceptance-l5/candidate/interrupted.json`。先前的目錄檢查仍被外部掃描標示；改為完全不接受 CLI 路徑、自動建立唯一目錄，
並以 3 個行為回歸驗證隔離、逃逸拒絕和不覆寫。
正式 benchmark／lifecycle／soak 結果待重新執行後補上。
原始紀錄保存於 `logs/acceptance-l5*`。


## 已完成的正式量測

核心與量測工具候選 `3fbd303`，來源指紋
`48ade239b7e55a15cb2cbc9bae1ea6aa759f0b8a0be0b93e875bd6a44f6f3b98`。

| 場景 | 週期 | p50／p95／p99（ms） | 最大值（ms） | 20 ms 超期率 | 結果 |
| --- | --- | --- | --- | --- | --- |
| fast | 10,000 | 1.17／2.00／2.41 | 22.82 | 0.01% | 通過 |
| hybrid | 10,000 | 1.70／2.60／3.09 | 22.54 | 0.01% | 通過 |

兩組均達到原先固定門檻；最大值超過單次週期有如實列入超期率，沒有隱藏或宣稱硬即時。
原始 triples 與報告位於 `logs/acceptance-l5/run-9z4hnses/`。

1,000 次含錄製的生命週期也通過：檔案描述符 3 → 3、程序執行緒 1 → 1、
DAM Python worker 最後為空；RSS 71,172,096 → 77,529,088 bytes。這不是 8 小時記憶體門檻的替代。
每次完成兩個 MCAP 檔並清除臨時資料；報告 `logs/acceptance-l5/run-5xvq20az/lifecycle.json`。

8 小時 hybrid soak 於 **2026-10-22 11:58:33 UTC** 啟動，但在約 5 分鐘後主動中斷：
交叉檢查發現 SDK cycle_id 比 runtime／慢速判定／MCAP 少 1。
重現測試先以 0 != 1 失敗，修正取值時機後完整 1337 測試通過，且跨 reset、慢速 verdict、
session／diagnostics 與錄製的週期 ID 一致。舊 soak 目錄 `logs/acceptance-l5/run-65t1g_2s/`
保留 interrupted 記錄；這些 benchmark／lifecycle 為前一候選基線，最終量測將全數重跑。

## 修正週期 ID 後的最終候選量測

候選 `019552a`，來源指紋 `82f9dae0d1beeceeae84f74b29a24e15a8586ca10c99154cb026b8836987a1de`。

| 場景 | 次數 | p99 | 20 ms 超期率 | 結果 |
| --- | --- | --- | --- | --- |
| fast | 10,000 | 2.469 ms | 0.01% | 通過 |
| hybrid | 10,000 | 3.070 ms | 0% | 通過 |
| recorded lifecycle | 1,000 | 不適用 | 不適用 | 通過；FD 3→3，threads 1→1，無殘留 DAM worker |

benchmark：`logs/acceptance-l5/run-vz2fwb4w/`；lifecycle：`logs/acceptance-l5/run-6hbxpf_g/`。
fast 最大呼叫時間 24.487 ms、最大排定完成時間 28.814 ms；hybrid 分別為 6.403／13.993 ms。
生命週期 RSS 70,221,824→77,512,704 bytes，兩個 MCAP／次均完成。

本次 8 小時 soak 於 **2026-10-22 12:20:31 UTC** 啟動，PID 54437，
`logs/acceptance-l5/run-o_jl6zg_/`。最早約 20:20:34 UTC 完成；目前仍待驗收，
每分鐘樣本不是完成報告，不能用前一候選結果代替。

## 2026-10-23 耐久重跑

舊 `run-o_jl6zg_` 最後樣本為 3,780 秒，程序已不存在且沒有完成報告；
中斷原因與退出碼不明，保留 `interrupted.json`，不算通過。核心指紋未變。
完整 8 小時重跑於 2026-10-23 09:08:33 UTC 啟動，目錄 `logs/acceptance-l5/run-xcuuwqcp/`，
最早約 17:08:36 UTC（洛杉磯 10:08:36）完成。獨立背景 supervisor 32301 的紀錄位於
`logs/acceptance-l5/detached-20261023/`，結束時寫入 `exit.json`，需與最終 soak 報告交叉驗證。
本次仍待驗收，沒有調寬門檻。

## 使用者要求縮短等待：停止長測

2026-10-23 使用者表示等待太久。已中斷 `run-xcuuwqcp`，暫停 `dam-2` 自動接續，
不再等待或自動重跑。已通過的 benchmark／lifecycle 與故障測試保留；8 小時耐久
改為延後驗證，部分分鐘樣本不能證明長期穩定性。本階段按縮短後範圍交付。
