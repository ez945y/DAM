# session-handoff.md — 會話交接摘要

> 最後更新: 2026-05-24

## 當前已驗證

- Live preview pipeline: 後端 WS coalescing + 前端 DOM bypass 已實作
- `tests/unit/test_live_preview.py` 24 tests passing
- Pre-commit hooks (ruff lint + format + mypy) 通過

## 本輪改動

### 代碼
- `dam/services/routers/telemetry.py` — WS send loop coalescing
- `dam-console/src/hooks/useTelemetry.ts` — live image 繞過 React state
- `dam-console/src/components/McapCameraPlayer.tsx` — LiveCameraCell 用 DOM event
- `dam-console/src/app/page.tsx` — 移除 liveImages prop
- `dam-console/src/lib/types.ts` — 移除 TelemetrySnapshot.liveImages

### 新增
- `tests/unit/test_live_preview.py` — 自動化測試
- `scripts/bench_live_preview.py` — standalone benchmark
- `scripts/bench_ws_e2e.py` — E2E WS benchmark
- `dam/services/ui/live_test.html` — raw WS 測試頁

### 基礎設施
- `CLAUDE.md` — 重寫為 DAM 專案 PM harness 指令
- `claude-progress.md` — 進度日誌（新建）
- `session-handoff.md` — 本檔案（新建）

## 仍損壞或未驗證

- Live preview 前端修正：等待用戶實機確認
- `make test` 完整套件：本輪未跑（只跑了 unit test subset）

## 下一步最佳動作

1. 用戶跑 `make dev`，開 console 看 live preview 是否流暢
2. 如仍有問題，用 `dam/services/ui/live_test.html` 隔離前端 vs 後端
3. 跑一次 `make test` 確認完整基線

## 命令速查

```bash
make dev                                    # 啟動開發模式
make test                                   # 完整測試
python -m pytest tests/unit/test_live_preview.py -v  # live preview 測試
python scripts/bench_ws_e2e.py              # E2E WS benchmark
# 打開 http://localhost:8080/static/live_test.html  # raw WS 測試頁
```
