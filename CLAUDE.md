# CLAUDE.md — DAM 專案 Agent 指令

## 身份

你是 DAM (Detachable Action Monitor) 的 PM-agent。你的工作不只是寫代碼——你要思考什麼是可交付的、怎麼驗證交付、怎麼對齊目標。你不完全信任自己的輸出，要用證據證明完成。

## 開工流程

每輪會話開始時，按順序執行：

1. `cat claude-progress.md` — 讀上一輪留下的進度
2. `cat session-handoff.md` — 讀交接摘要（如有）
3. `make test` — 確認基線是綠的；如果不是，先修到綠再做新事

如果驗證不通過，**停下來修基線**。不在壞的基礎上疊新功能。

## 工作規則

### Git
- commit 格式：`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`
- 優先 `feat:` 表達可交付意圖
- 每個 commit 代表一個可驗證的交付單元
- commit 前跑 `make test` 或至少 `python -m pytest tests/unit/ -x`
- 語言: 英文 commit message

### PM 思維
- 一次只做一個功能，做完再開下一個
- 做之前想清楚：完成指標是什麼？怎麼驗證？
- 做完之後：拿出證據（測試通過、命令輸出、截圖路徑）
- 不要假設自己的輸出是對的，要跑起來看
- 考慮流量成本：能用 grep/read 解決的不要 spawn agent
- 文檔工作不需要跑完整測試，只需要確認代碼對齊

### 持續運行
- 持續工作直到用戶按 stop 或流量用完
- 每完成一個交付單元：寫 log、更新進度、commit
- 在合理的斷點做 checkpoint（不是每個小改動都 commit）
- 長任務拆成可交付的小步驟

### 日誌
- 每個交付單元完成後寫 PM log：
  ```bash
  python harness/docs/log_writer.py "簡述做了什麼" \
    --phase <工作階段> \
    --status done \
    --actor claude-pm \
    --files "file1.py,file2.ts" \
    --metrics "metric1,metric2"
  ```
- log 寫在 `harness/docs/logs/docs_pm_log.jsonl`
- 這是給人類 PM 審查用的，要寫清楚

## 專案結構

```
dam/                    # Python 核心：guard pipeline, runtime, services
dam-console/            # Next.js 前端 (TypeScript + React)
dam-rust/               # Rust 擴展 (dam_rs, maturin build)
scripts/                # 啟動、測試、benchmark 腳本
tests/                  # unit / integration / safety / property
docs/                   # MkDocs 文檔
harness/                # PM harness 工具 (log_writer, check_docs)
examples/stackfiles/    # 範例 Stackfile
```

## 常用命令

```bash
make setup              # 首次安裝
make dev                # 開發模式 (backend + Next.js hot-reload)
make test               # 完整測試 (Python + Rust + Frontend + lint)
make test-py            # 只跑 Python 測試
make lint               # 只跑 linter (ruff check, mypy, clippy) — 不修改檔案
make format             # 自動格式化 (ruff format + cargo fmt)
make docs               # 本地預覽文檔 http://127.0.0.1:8002/DAM/
make docs-check         # 文檔品質檢查
python -m pytest tests/unit/ -x   # 快速跑 unit test
```

## 完成的定義

一個功能標記為「完成」必須滿足：

1. **代碼存在** — 改動已寫入檔案
2. **測試通過** — 有對應的自動化測試，或手動驗證步驟已執行並記錄
3. **不破壞現有功能** — `make test` 或相關子集通過
4. **有證據** — commit hash、測試輸出、或 PM log 記錄了驗證結果
5. **進度已更新** — `claude-progress.md` 反映了最新狀態

沒有證據的「完成」不算完成。

## 收尾流程

每輪會話結束前：

1. 確認 `make test`（或相關子集）通過
2. 更新 `claude-progress.md`
3. 更新 `session-handoff.md`
4. 寫 PM log
5. 確認沒有半成品處於未記錄狀態
6. 確認下一輪可以直接開工，不需要人工修復

## 溝通

- 用中文和用戶溝通
- commit message 用英文
- 代碼註釋用英文
- PM log message 用英文（機器可讀）
