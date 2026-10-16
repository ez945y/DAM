# DAM Agent 入口

DAM 的 agent 工作流程集中於 repository 的 `skills/dam-agent/SKILL.md`，原始碼查證入口與離線驗證範例位於 `skills/dam-agent/references/project-map.md`。

將整個 `skills/dam-agent` 資料夾放到 `~/.codex/skills/` 後，可用 `$dam-agent` 呼叫。其他 agent 可直接讀取同一份 `SKILL.md`。專案維護規則仍以根目錄的 `CLAUDE.md` 及適用的 agent 指示為準。

這個入口不再維護另一套 API、schema 或執行順序描述，避免與實作分歧。依需求查閱：

- [Library API](library-api.md)：Guardrail 的 dict 輸入、action 輸出、型別與資源生命週期。
- [CLI](cli.md)：設定驗證、檢視與離線記錄分析。
- [Stackfile Guide](quick-stack.md)：邊界、task 與硬體配置。
- [Troubleshooting](getting-started/troubleshooting.md)：環境、連接埠與 task 問題。
- [Hardware Readiness](getting-started/hardware-readiness.md)：實機準備。

API 與 schema 的細節應對照目前 checkout 的原始碼和測試；歷史規格與交接紀錄提供背景，不作為現行介面的保證。
