# DAM 後續功能與穩定性規劃

更新日期：2026-10-16。規劃以 `8b99129` 的 SDK 與 runtime 為基準；R01 已於 `caba57b`、R02 已於 `a18ce9c` 完成，其餘項目維持待辦。

下一階段優先補齊安全判定從輸入、非同步評估到輸出的完整流程，再改善 SDK 的設定、診斷與部署體驗。除明確標示完成者外，下列項目都是待辦建議，不代表已實作功能或發行承諾。先完成 P0 的可重現測試與修正，再擴充 API，避免新功能建立在不明確的執行語意上。

## 已完成的基礎

| 範圍 | 已完成行為 | 對應 commit |
| --- | --- | --- |
| 安全條件更新 | 節點參數與共用參數一起更新；失敗保留舊設定；保留目前階段與計時 | `f405deb` |
| 決策與合併 | callback 缺失、錯誤回傳、無效修正與合併衝突產生 FAULT | `f405deb` |
| 輸出與 fallback | 合併 FAULT 納入 fallback 判定；輸出尺寸檢查；保留速度、夾爪與時間戳 | `1cf4536` |
| 非同步判定 | 過期時間包含快照排隊與計算時間；例外回報 FAULT；停止逾時阻擋任務切換 | `09a1a6b` |
| SDK 生命週期 | context manager、可重複 close、初始化失敗清理、單次呼叫清理、processor reset 清理 | `8b99129` |
| SDK 輸入體驗 | list 回傳 list、浮點 Tensor 轉換、必要觀測欄位提示、缺少 observation 時阻擋 | `8b99129` |
| 非同步首次判定 | 首輪有期限等待或未就緒即拒絕；單次 helper 等待自己的判定；公開 readiness 與判定所屬週期；正常 runtime 控制迴圈在送出前套用慢速結果 | `caba57b` |
| 合併約束完整性 | PASS 與 CLAMP 模型共同求解；不可行時 FAULT；分階段傳遞修正，最終候選再以模型核對且不重跑 callback | `a18ce9c` |

2026-10-16 R02 獨立工作目錄的 Python 非硬體回歸為 994 項通過（R01 為 968 項）；型別、提交檢查與嚴格文件建置通過。這些結果不涵蓋 GPU、實機或安全認證。

## 優先順序

| 編號 | 優先級 | 開發項目 | 完成後的使用體驗 |
| --- | --- | --- | --- |
| R01 | P0／已完成 | 首輪與單次呼叫的非同步判定語意 | 可區分當次與快取的慢速判定；缺少首次判定不再默默放行 |
| R02 | P0／已完成 | 合併後完整約束驗證 | 合併納入 PASS 約束；最終命令破壞已驗證條件時回報 FAULT |
| R03 | P0 | 非同步快照與評估狀態隔離 | 背景檢查讀到一致資料，不受後續修改干擾 |
| R04 | P0 | Runtime 最終輸出與 fallback 契約 | 所有送出路徑都有一致的格式與有效值檢查 |
| R05 | P1 | SDK 結構化結果與錯誤分類 | 不必讀私有欄位，就能判斷失敗原因與處理方式 |
| R06 | P1 | 公開設定建構與預檢 API | 不接硬體即可確認設定、callback、solver 與輸入契約 |
| R07 | P1 | 可觀測的設定重載與 session 切換 | 清楚知道更新是否成功、何時生效及是否需要重啟 |
| R08 | P1 | 安裝與相容性驗證矩陣 | 從乾淨環境安裝後即可重現官方範例 |
| R09 | P2 | 診斷、效能與長時間穩定性工具 | 能定位延遲、資源洩漏與偶發故障 |
| R10 | P2 | 完整 SDK 範例與版本遷移指南 | 從最小範例逐步接入實際控制迴圈 |

## P0 執行安全完整性

### R01 首輪與單次呼叫的非同步判定

狀態：已完成，commit `caba57b`（2026-10-16）。

先以整合測試確認：啟用 slow lane 的 L2 callback 固定 REJECT 時，原有 session 首輪與 `dam.guardrail()` 均回傳原始命令；首輪 callback 拋錯也未參與當次決策。另發現正常 runtime `step()` 因關閉重複錄製等副作用而跳過慢速提交與判定，已一併修正。

完成行為：

- `slow_lane.first_verdict` 預設 `wait`，以 `verdict_timeout_ms`（預設 500 ms）限制等待；`reject` 策略在首次判定尚未完成時立即拒絕。即使 `stale_action: warn`，也不再提供未檢查的首輪寬限。
- `dam.guardrail()` 一律等待自己的慢速判定；長期 `Guardrail` 可用 `wait_for_slow=True` 每輪等待，預設則在取得首次判定後使用最新快取。等待取得的結果仍須符合 `max_staleness_ms`。
- 快速 callback 只執行一次，慢速檢查接收快速修正後的候選命令；最終決策、風險記錄、歷史提交與正常控制迴圈的 sink 輸出都在慢速 gate 之後。
- 公開 `slow_lane_status`（disabled／pending／ready／stale／bypassed），慢速結果附 `basis_cycle_id`、`verdict_age_ms` 與 `current_cycle_checked`。ready 只表示已有新鮮結果，REJECT／FAULT 也可為 ready，不代表安全通過。
- 未就緒與等待逾時保留可辨識的 REJECT 結果；ENFORCE 使用既有 fallback，MONITOR 記錄但不攔截，LOG_ONLY 跳過檢查。

驗證紀錄：

| 指令 | 結果 |
| --- | --- |
| `.venv/bin/python -m pytest tests -m 'not hardware and not ros2' -q` | 968 passed |
| `.venv/bin/python -m pytest tests/integration/test_slow_lane_readiness.py tests/unit/test_slow_lane.py -q` | 最後複驗 70 passed |
| `.venv/bin/python -m mypy --config-file pyproject.toml dam tests` | 219 source files 通過 |
| `.venv/bin/python scripts/check_docs.py` | 文件文字檢查與 MkDocs strict build 通過 |
| `git commit` 的 pre-commit hooks | whitespace、檔案大小、Ruff lint／format、mypy 通過；無 Rust 變更，Rust hooks 跳過 |

測試涵蓋固定 REJECT、pending、timeout、callback 例外、過期 PASS、首次等待與每輪等待、快取來源週期、任務重啟 readiness、MONITOR／LOG_ONLY、單次清理、快速 CLAMP 只評估一次與 runtime sink 前拒絕。

相容性：未啟用 slow lane 的同步語意不變；啟用後首次呼叫預設可能增加等待時間，未就緒時不再放行。頻率與等待／過期期限必須是有限正值。Python callback 無法強制取消，等待逾時不等於 worker 已停止；`close()` 仍可額外等待停止期限並拋出 `TimeoutError`。此輪在本機 macOS／Python 3.12 驗證，未驗證其他 OS／Python 版本、CUDA／MPS 或實機。完整約束驗證、快照所有權與 fallback context 的完整輸出路徑仍屬 R02–R04。

主要位置：`dam/api.py`、`dam/config/schema.py`、`dam/runtime/slow_lane.py`、`dam/runtime/guard_runtime.py`、`dam/runtime/execution_engine.py`、`dam/runtime/context.py`。行為說明已同步至 [SDK 文件](library-api.md) 與 [安全模型](concepts/safety.md)。

### R02 合併後完整約束驗證

狀態：已完成，commit `a18ce9c`（2026-10-16）。

先重現三項問題：原本 PASS 的速度限制會被位置修正破壞、耦合約束未參與共同求解，以及互斥上下界被軟化為仍違規的命令。另修正分階段執行時，後一階段未收到前一階段修正命令的問題。

完成行為：

- 內建 L1 callback 在 PASS 與 CLAMP 時皆提供有效約束模型。MotionGuard 合併全部模型，以硬約束求解；無解、求解失敗、模型或結果無效時回報 FAULT。
- 合併後與 runtime 最終候選均執行純模型核對，不重複呼叫 callback，因此不會因重驗而重複推進歷史或計數。末端速度另檢查完整向量範數，避免只滿足切平面卻超速。
- 無模型的 L1／L2 結果綁定原先接受的完整命令；後續修正若改變它，無法證明仍符合原判定時回報 FAULT。單一無模型 CLAMP 仍可使用，但不能與不具足夠證據的其他修正任意合併。
- 同週期的慢速結果參與最終候選驗證；舊週期快取仍依 R01 作為 gate，不改稱為當次命令的完整證明。
- 線性約束先按每列最大係數正規化，再以 `1e-7` 容差檢查；不再截掉超出命令維度的約束欄位。離線 replay 測試固定主機健康輸入，避免實際主機負載影響預期決策。

驗證紀錄（以 `5a8bbf2` 加本次變更的獨立工作目錄驗證，未混入其他進行中修改）：

| 指令 | 結果 |
| --- | --- |
| `.venv/bin/python -m pytest tests -m 'not hardware and not ros2' -q` | 994 passed |
| `.venv/bin/python -m mypy --config-file pyproject.toml dam tests` | 220 source files 通過 |
| `.venv/bin/python scripts/check_docs.py` | 文件文字檢查與 MkDocs strict build 通過 |
| `git commit` 的 pre-commit hooks | whitespace、檔案大小、Ruff lint／format、mypy 通過；無 Rust 變更，Rust hooks 跳過 |

新增測試涵蓋原本 PASS 被破壞、可行耦合約束、互斥上下界、QP 無解／錯誤候選、非法維度與非有限值、數值容差與不同尺度、末端速度切平面漏檢、跨 L1／L2 修正、callback 只呼叫一次，以及同週期與快取慢速結果的區別。

相容性：先前靠放鬆限制才能輸出的命令現在會 FAULT，ENFORCE 進入既有阻擋／fallback 流程；MONITOR 仍遵循原有觀測模式。`slack_weight` 欄位保留相容性，但 enforced motion fusion 不再放鬆限制。自訂 callback 若要支援後續修正，需提供可驗證模型。此輪保證的是已提供的有效模型；停用／缺資料而略過的檢查、局部線性化的物理誤差與舊週期快取不構成完整安全證明，fallback 輸出契約仍由 R04 處理。本機 macOS／Python 3.12 已驗證，其他 OS／Python 版本、CUDA／MPS 與實機未驗證。

主要位置：`dam/boundary/callbacks/kinematics.py`、`dam/guard/pipeline.py`、`dam/guard/aggregators/motion_qp.py`、`dam/runtime/qp_solver.py`、`dam/runtime/execution_engine.py`。行為說明已同步至 [SDK 文件](library-api.md) 與 [安全模型](concepts/safety.md)。

### R03 非同步快照與狀態隔離

Frozen dataclass 並不會自動讓其中的 NumPy 陣列、images 或 metadata 成為不可修改資料。需建立 buffer 所有權規則，並檢查 slow-lane 評估期間是否讀取到可變的 runtime 狀態。

建議採用明確複製或唯讀 buffer 策略，將 task、設定版本與輸入快照綁定；評估結果應攜帶相同版本。大型影像的複製成本需實測，再選擇安全的共享方式。

驗收條件：submit 後修改呼叫端陣列、影像或 metadata，不改變當次判定；任務切換與重載期間的舊版本結果不可套用到新版本；壓力測試能穩定重現並排除競態。

主要位置：`dam/runtime/slow_lane.py`、`dam/types/observation.py`、`dam/types/action.py`。

### R04 Runtime 輸出與 fallback 契約

SDK 已有輸入檢查，但直接使用 runtime、自訂 guard、fallback context 與 sink 的路徑仍需一致的端到端契約。應盤點原始 action、修正 action、hold、retreat 與 emergency-stop 的輸出路徑。

建議在送往 sink 前統一驗證尺寸、有限值、可選速度與夾爪欄位，並明確定義 MONITOR／LOG_ONLY 對格式錯誤的行為。格式錯誤與一般安全限制違反應分開處理，避免觀測模式意外放行無法執行的資料。

驗收條件：各輸出路徑注入 NaN、Infinity、錯誤形狀、缺少觀測、sink 例外及 fallback 例外；不把無效命令標記為已成功送出，且錯誤保留在 cycle 結果中。

主要位置：`dam/runtime/execution_engine.py`、`dam/runtime/builtin_contexts.py`、`dam/runtime/guard_runtime.py`。

## P1 SDK 與部署體驗

### R05 結構化結果與錯誤分類

保留目前 callable 回傳命令的介面，另設計 opt-in 詳細結果 API。結果可包含命令、PASS／CLAMP／REJECT／FAULT、fallback、boundary、原因、設定版本與判定資料年齡。此 API 尚未實作，名稱需在設計時確認。

錯誤分類至少區分設定錯誤、輸入契約錯誤、已關閉 session、背景 worker 逾時與執行失敗；保留原始例外鏈。

驗收條件：使用者只靠公開 API 就能處理上述情況；詳細結果與既有 `last_results` 一致；有序列化範例及相容性測試。

### R06 公開設定建構與預檢

提供公開的設定物件或 mapping 建構入口，避免 SDK 使用者依賴 `_from_config` 等私有方法。提供不建立硬體連線的預檢，回報 callback／solver 缺失、action layout、必要觀測欄位、單位與 fallback 契約。

驗收條件：檔案與設定物件建構結果一致；預檢不啟動 worker、不寫錄製檔、不移動機器人；錯誤能指出設定欄位及可用選項。

### R07 重載回報與 session 切換

目前只支援節點 `params` 重載；結構變更，以及 slow lane 運行中的重載，會被拒絕。先提供公開的 accepted／applied／rejected 狀態、原因與版本，再考慮擴充可重載範圍。

建議新增正式 task 切換與 session reset 介面，明確定義哪些歷史、計時、錄製與 worker 狀態會保留。非同步熱更新應先完成 R03 的版本隔離。

驗收條件：使用者不必讀 log 才知道是否更新成功；失敗可重試且不混用新舊設定；重複 close／reset、切換逾時與回復均有測試。

### R08 安裝與相容性矩陣

在乾淨環境驗證 wheel 安裝、版本查詢、基本 import、SDK 範例及 Rust 擴充載入。評估是否將部分大型 adapter 依賴移到 extras；這會影響既有安裝契約，應另外設計遷移方式。

驗收條件：覆盖宣告支援的 Python 版本與作業系統；Tensor 測試包含 CPU、可用的 CUDA／MPS、requires-grad、bfloat16、dtype 溢位及 device 保留。未具備硬體的項目須標示未驗證，不得算作通過。

## P2 診斷與文件

### R09 可觀測性與長時間測試

提供每層延遲、慢速判定年齡、拒絕率、設定版本與資源數量的診斷快照。新增長時間反覆建立、執行、reset、close 的測試，以及 callback 超時、錄製失敗與 solver 故障的注入場景。

驗收條件：可定位延遲來源；量測 p50／p95／p99 與期限違反率；測試結束後 worker、檔案描述符與記憶體不持續增加。效能門檻需由目標平台量測後訂定。

### R10 SDK 範例與遷移指南

建立可直接執行的最小範例：純 NumPy、PyTorch、mobile base、機械臂、processor、錄製與錯誤復原。說明單次 helper 與長期 session 的適用情境，並提供舊介面到新介面的對照。

驗收條件：每個範例在 CI 有不接硬體的 smoke test；使用公開 API、明確清理資源；需要實機的步驟另行標示並使用有限週期。

## 後續執行方式

建議依序安排：R01 → R02 → R03／R04 → R05／R06／R07 → R08 → R09／R10。這是依風險與依賴排序，不是時程估算；R08 的安裝 smoke test 與 R10 的文件可提早逐步補上。

每個項目開始時先重讀相關實作與既有測試，建立能重現問題的案例，再決定改動範圍。完成時記錄測試指令、結果、行為相容性與仍未驗證的平台；更新本文件的狀態並附 commit。API 行為改變需同步更新 [SDK 文件](library-api.md) 與 [安全模型](concepts/safety.md)。

本輪不承諾安全認證、任意配置無縫熱更新、零延遲或所有硬體通用的停止策略。新功能仍需在目標平台與受監督的實機流程中驗證。
