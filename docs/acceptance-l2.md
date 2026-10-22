# L2／R06 設定建構與靜態預檢

日期：2026-10-22。基準為 L1 提交 `e31e837` 加本階段差異。

## 交付

`Guardrail`／`guardrail` 可接受字串或 Path 路徑、mapping、公開的 StackfileConfig。
三種來源採相同驗證與建構流程；mapping/model 先複製，外部修改不改變已建立 session。

公開 `dam.preflight()` 回傳 PreflightReport／PreflightIssue，包含欄位定位、錯誤與警告、
task、callback 必需觀測群組、joint names、命令長度/layout 與關節單位。
檢查 schema、callback、solver factory／reference、命令鍵／維度、宣告單位及 fallback。
未知 factory、缺少 retreat 目標、非關節 layout 配置位置 fallback、非有限 fallback
參數與無效 scale 都可在建構前辨識。未宣告非關節單位明確警告，不猜測語意。

預檢只讀設定／preset、登錄及檢查靜態宣告，不建立 runtime、連硬體、建 solver、
執行 callback 或自訂 fallback 建構函式，不啟 worker 或錄製。測試以會立即失敗的
替身監視上述入口，確認沒有呼叫。

## 驗證與限制

- 全套非硬體 Python：**1306 passed in 39.47s**，退出碼 0；`logs/acceptance-l2-python.log`。
- 測試涵蓋來源等價、來源所有權、無資源副作用、schema 欄位、未知 callback／solver、
  無效 layout／solver reference、單位、fallback 和未知 task。
- `python -m examples.guardrail_preflight` 通過；`logs/acceptance-l2-example.log`。
- Ruff／format 通過；完整 pre-commit 首次發現區域變數重用的 mypy 錯誤，改名後重跑。
  最終靜態與文件結果記錄於交接與 `logs/acceptance-l2-precommit.log`／`logs/acceptance-l2-docs.log`。

`valid` 僅表示沒有靜態錯誤。預檢不執行 solver，因此不能證明 solver 初始化成功、
部署資產存在、callback 正確或 fallback 的物理安全性；這些限制也寫入公開 API 文件。
普通建構不自動執行額外預檢。macOS arm64／Python 3.12；未做實機或跨平台驗證。

下一階段：L3/R07 公開重載收據與 session 生命週期。
