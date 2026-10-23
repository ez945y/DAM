# L1／R05 公開結果與錯誤分類

日期：2026-10-22。交付基準：短期候選版 `03e3e16` 加本階段差異。

## 交付與驗收

- `Guardrail.evaluate()` 回傳公開 `GuardrailEvaluation`，只執行一次驗證；
  callable 仍回傳同表示方式的命令。PASS／CLAMP 與 callable 輸出一致。
- 結果包含 decision、guard/boundary 名稱與原因、fallback、設定版本、task、cycle、
  實際消費判定的年齡與 `output_status=returned`。SDK 不宣稱有設備送出。
- REJECT／FAULT 帶明確 fallback；MONITOR 的 REJECT 不誤稱已使用 fallback。
- 詳細證據與 runtime 原始資料分離，後續 metadata 修改不改變已回傳證據。
- `to_dict()`／`to_json()` 可序列化命令與證據；公開範例
  `python -m examples.guardrail_evaluation` 已離線執行，正常退出。
- 公開錯誤分類：ConfigurationError、InputContractError、SessionClosedError、
  OperationTimeoutError、ExecutionError，皆繼承 DamError；包裝錯誤保留 `__cause__`。
  輸入錯誤後可繼續使用 session，close timeout 保留可重試行為。

## 證據

Python 完整非硬體測試 **1287 passed in 34.69s**，退出碼 0，
`logs/acceptance-l1-python.log`。此前針對性測試 86 通過；不與全套重複累計。
局部 mypy、Ruff 通過；完整 pre-commit 與文件檢查結果於交接紀錄保存。
可執行範例輸出位於 `logs/acceptance-l1-example.log`。

本階段沒有更改 Console 或 Rust。平台仍為 macOS arm64／Python 3.12；沒有實機驗證。
呼叫端須改抓公開錯誤類別，底層 KeyError／FileNotFoundError 等保留為原因鏈，
不再作為 SDK 的主要錯誤介面。Guard 的拒絕／故障與回傳 fallback 仍屬結果，
不因新增分類改成拋出執行錯誤。

下一階段：L2/R06 mapping／設定物件建構與無副作用預檢。
