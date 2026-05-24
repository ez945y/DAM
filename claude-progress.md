# claude-progress.md — 進度日誌

## 當前已驗證狀態

- **專案**: DAM (Detachable Action Monitor) v0.5.0
- **倉庫根目錄**: `/Users/chenyizhong/Documents/Claude/Projects/Security Guard.nosync`
- **標準啟動路徑**: `make dev`
- **標準驗證路徑**: `make test`
- **基線狀態**: unit tests passing（截至 2026-05-24）

## 當前最高優先級未完成功能

1. **機器人微振盪**：模型輸出高頻方向翻轉（~60% cycles 有 sign change），幅度 <1° 所以 guard PASS，但肉眼抖動明顯。這不是 guard pipeline 問題，是模型輸出問題。可能需要 action smoothing / EMA filter。
2. **Guard Status 前端驗證**：fix 已 commit 但需要重建前端 (`cd dam-console && npm run build`) 才生效。用戶尚未確認。
3. **MCAP 回讀 Risk Log**：用戶提到想從 MCAP 讀取歷史 risk data，尚未實作。

## 當前 blocker

無

## 會話記錄

### Session 2026-05-24 #1 (live preview + guard status + risk log)

- **本輪目標**: 修復 live mode 卡頓、Guard Status 消失、Risk Log 太少、機器人抽動
- **已完成**:
  - Live preview: 前端 DOM bypass + 後端 WS coalescing（24 tests）
  - Guard Status: `gGuardMap` 改為每 cycle 全量替換，不再 upsert 殭屍 guard
  - Risk Log: 只記錄 notable cycles（clamp/reject/fault/context change），容量 1k→10k
  - PM Harness: CLAUDE.md + progress + handoff + init.sh + checklist + format target
  - Harness 整合: `harness/` 併入 `scripts/`，PM log 移至 `logs/`
  - MCAP 數據分析：確認最新 session 無爆衝，但模型有高頻微振盪
- **執行過的驗證**:
  - `pytest tests/unit/test_live_preview.py` — 24/24 passed
  - `pytest tests/unit/test_services.py -k risk` — 20/20 passed
  - `npm run test:ci` — 99/99 passed
- **已記錄證據**: commits `17f8641`, `ac4cfc9`, `6a45c65`, `49cea81`, `4d5993f`
- **提交記錄**:
  - `fix: decouple live camera images from React state to eliminate UI freezes`
  - `feat: establish PM harness and split make format from make test`
  - `feat: consolidate harness/ into scripts/ and refine CLAUDE.md with PM philosophy`
  - `fix: guard status stale after context switch to fallback`
  - `feat: risk log only records notable cycles, increase capacity to 10k`
- **已知風險或未解決問題**:
  - Guard Status fix 需要前端 rebuild 才生效
  - 模型微振盪不是 guard 問題，需要 action smoothing
  - MCAP 回讀 risk log 未實作
  - `make test` 完整套件本輪未跑
- **下一步最佳動作**: 見 session-handoff.md
