# clean-state-checklist.md — 收尾檢查清單

每次會話結束前逐項檢查。確保倉庫處於下一輪可以直接開工的狀態。

## 檢查項

- [ ] `make test`（或相關子集）通過
- [ ] `claude-progress.md` 已更新，反映本輪成果
- [ ] `session-handoff.md` 已更新（長會話或多區域並行時）
- [ ] PM log 已寫（`harness/docs/logs/docs_pm_log.jsonl`）
- [ ] 沒有半成品處於未記錄狀態
- [ ] 沒有暫存的 debug 代碼留在 codebase
- [ ] git status 乾淨（改動已 commit 或明確標記為 WIP）
- [ ] 下一輪會話不需要人工修復就能 `cat claude-progress.md` 然後開工
