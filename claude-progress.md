# claude-progress.md — 進度日誌

## 當前已驗證狀態

- **專案**: DAM (Detachable Action Monitor) v0.5.0
- **倉庫根目錄**: `/Users/chenyizhong/Documents/Claude/Projects/Security Guard.nosync`
- **標準啟動路徑**: `make dev`
- **標準驗證路徑**: `make test`
- **基線狀態**: 通過（截至 2026-05-24）

## 當前最高優先級未完成功能

（待填寫 — 下次會話開工時由用戶指定或 agent 從上下文推斷）

## 當前 blocker

無

## 會話記錄

### Session 2026-05-24 #1 (live preview fix)

- **本輪目標**: 修復 live mode 相機畫面卡頓
- **已完成**:
  - 診斷出根因：前端每幀 binary WS message 觸發 React 全頁 re-render
  - 後端：WS send loop 加入 drain-and-coalesce（只發最新 cycle + 每 camera 最新 binary）
  - 前端：live image 改為 DOM CustomEvent → 直接 img.src 更新，繞過 React state
  - 新增 24 個自動化測試 `tests/unit/test_live_preview.py`
  - 新增 benchmark 腳本 `scripts/bench_live_preview.py`, `scripts/bench_ws_e2e.py`
  - 新增原始 WS 測試頁 `dam/services/ui/live_test.html`
- **執行過的驗證**: `python -m pytest tests/unit/test_live_preview.py -v` — 24/24 passed
- **已記錄證據**: commit `17f8641`
- **提交記錄**: `fix: decouple live camera images from React state to eliminate UI freezes`
- **已知風險或未解決問題**: 前端修正需要實機驗證（user 尚未確認畫面是否流暢）
- **下一步最佳動作**: 用戶啟動 `make dev` 實測 live preview 是否流暢
