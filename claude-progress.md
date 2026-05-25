# claude-progress.md — 進度日誌

## 當前已驗證狀態

- **專案**: DAM (Detachable Action Monitor) v0.5.0
- **倉庫根目錄**: `/Users/chenyizhong/Documents/Claude/Projects/Security Guard.nosync`
- **標準啟動路徑**: `make dev`
- **標準驗證路徑**: `make test`
- **基線狀態**: unit tests passing（截至 2026-05-24）

## 當前最高優先級未完成功能

1. **Vision OOD 閾值校準**：Vision fusion 跨場景分離力極好（abnormal detection=100%），但 mean+3σ 閾值策略過於保守導致 FPR 過高。需改用 percentile-based 閾值或 normal_test 校準。
2. **機器人微振盪**：模型輸出高頻方向翻轉（~60% cycles 有 sign change），幅度 <1° 所以 guard PASS，但肉眼抖動明顯。這不是 guard pipeline 問題，是模型輸出問題。可能需要 action smoothing / EMA filter。
3. **Guard Status 前端驗證**：fix 已 commit 但需要重建前端 (`cd dam-console && npm run build`) 才生效。用戶尚未確認。
4. **MCAP 回讀 Risk Log**：用戶提到想從 MCAP 讀取歷史 risk data，尚未實作。

## 當前 blocker

無

## 會話記錄

### Session 2026-05-25 #3 (Read-only MCAP Incident Triage)

- **本輪目標**: 將實機「不動／被擋」排查整理成 agent 可安全重複使用的唯讀工具
- **已完成**:
  - 新增 `scripts/mcap_triage.py`：預設選最新 MCAP、提供 human/JSON 輸出、唯讀 status GET、known-good session 起始姿態比較
  - 同時支援 Rust combined msgpack 與 Python split-topic JSON session，不建立 session SQLite cache
  - 報告區分 clamp、reject/no validated command、與「validated command sent but little observed response」關節
  - 將 `scripts/joint_diagnostics.py` 預設改為分析最新現有 MCAP；只有顯式 `--run` 才能建立新控制 session
  - 更新 agent 指引與 loopback 文件，規定實機 incident 第一動作必須唯讀
- **執行過的驗證**:
  - `.venv/bin/ruff check scripts/mcap_triage.py scripts/joint_diagnostics.py tests/unit/test_mcap_triage.py` — passed
  - `.venv/bin/python -m pytest tests/unit/test_mcap_triage.py tests/unit/test_mcap_session_parsing.py tests/unit/test_lerobot_adapters.py tests/unit/test_lerobot_builder.py -q` — passed
  - 以最新實機 MCAP `session_a28b7324_1779707257.mcap` 執行 human/JSON smoke run — 成功讀取 848 cycles，正確顯示全程 clamp 但未誤報無響應關節
- **已知風險或未解決問題**:
  - 最新 session 仍為 `joint_velocity_limit` 與 `task_gripper_sequence` 每幀 clamp；triage 僅提供證據，不應自行改安全參數
  - ACT gripper unit 與 task gripper threshold 的一致性仍待獨立修復與驗證

### Session 2026-05-25 #2 (RQ1 Pipeline Cleanup)

- **本輪目標**: 將 RQ1 從混用私有 guard state 的實驗腳本整理成可追蹤、可重現的 offline evaluation pipeline
- **已完成**:
  - RQ1 改用 `OODContext` + 公開 `RealNVPFlowBackend` / `MemoryBankBackend` / `WelfordBackend`
  - Real-NVP backend 保留 verbose training progress；runner 不再直接操作 `_flow`
  - Feature seed 與 vision configuration 納入 flow cache key；實測第二次 run 命中 dataset + model cache
  - Vision subsampling 改為只評估真正有 image frame 的 observation，避免零向量混入 vision 結果
  - Summary/UI 顯示實際輸入 signal、未計分的 `action` 與 vision frame attachment counts
  - 補齊 trajectory diagnostic implementation 與 cache configuration test
  - 文檔改為準確描述 RQ1 是共享 DAM feature/backend API 的 offline harness
- **執行過的驗證**:
  - `.venv/bin/python -m pytest tests/unit/ -x -q` — 553 passed
  - `.venv/bin/ruff check scripts/run_l0_calibration.py dam/guard/ood_backend.py dam/experiments/registry.py tests/unit/test_l0_calibration_features.py` — passed
  - `cd dam-console && npm run build` — passed
  - `.venv/bin/python scripts/check_docs.py` — passed
  - RQ1 state-only UI smoke run (1 epoch, full cached datasets) — summary refreshed and explicitly reported `not scored: action`; abnormal detection=3.7%, confirming this configuration is not adequate
  - RQ1 cache smoke run (60 observations, 1 epoch, repeated invocation) — second run reported model and dataset cache hits
- **已知風險或未解決問題**:
  - `action` 尚未作為 Real-NVP feature input；recover-failure 的同場景異常不能依靠目前 state-only 路徑解決
  - State-only full-dataset smoke run 的異常 detection 很低，不能作為可部署結果
  - Vision threshold calibration 仍需以有效 validation split 調整
- **下一步**:
  - 定義並實作 `state + action + temporal` RQ1 feature mode，使用 held-out validation 校準 threshold，再與 vision-fused mode 對照

### Session 2026-05-25 #1 (Vision Feature Extraction Integration)

- **本輪目標**: 整合 HuggingFace pretrained vision feature extractor 到 L0 OOD guard pipeline
- **已完成**:
  - VisionFeatureExtractor: MobileNetV3 large/small backbone, HW/C→embedding 萃取
  - LeRobotVideoLoader: mp4 video decode for lerobot v3 datasets (PyAV)
  - OODContext fusion: configure_vision() + 256-dim fused features (joint+vision weighted)
  - Boundary callbacks: vision_model/vision_weight 參數支援
  - Frontend: OODTrainer 加入 Vision Model 選擇器 + Weight 滑桿
  - Backend: trainer service + router 透傳 vision params
  - Calibration script: --vision-model/--vision-weight/--vision-camera CLI
  - RQ1 實驗驗證: cross-scene (feeding-nuts vs fmb-v2) abnormal detection=100%
  - Unit tests: 11 tests (VisionFeatureExtractor + OODContext vision integration)
- **執行過的驗證**:
  - `pytest tests/unit/ -x` — 550 passed (1 pre-existing failure excluded)
  - `npx jest --ci` — 99/99 passed
  - `npx tsc --noEmit --skipLibCheck` — no errors
  - RQ1 calibration with vision: abnormal detection rate = 1.0
- **已記錄證據**: commits `f93eb81`, `384d982`
- **已知風險或未解決問題**:
  - FPR 過高 (87% normal, 99% legal): 閾值策略 (mean+3σ) 不適合 256-dim fused space. 特徵分離實際上很好 (normal median=-505.8 vs abnormal median=-436.4, gap=69 NLL units), 問題在於 calibration 策略太保守
  - 同場景不同動作的視覺區分力低（如預期）— vision 是輔助信號不是主力
  - Theia model 支援已寫但未驗證（需 transformers 套件）
- **下一步**:
  - 改善閾值校準策略（percentile-based 或 normal_test calibration）
  - 文檔更新（使用者可見行為改變）

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
