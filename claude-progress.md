# claude-progress.md — 進度日誌

## 當前已驗證狀態

- **專案**: DAM (Detachable Action Monitor) v0.5.0
- **倉庫根目錄**: `/Users/chenyizhong/Documents/Claude/Projects/Security Guard.nosync`
- **標準啟動路徑**: `make dev`
- **標準驗證路徑**: `make test`
- **基線狀態**: unit tests passing（截至 2026-05-26, 668 passed + 109 frontend）

## 當前最高優先級未完成功能

1. **Vision OOD 閾值校準**：Vision fusion 跨場景分離力極好（abnormal detection=100%），但 mean+3σ 閾值策略過於保守導致 FPR 過高。需改用 percentile-based 閾值或 normal_test 校準。
2. **機器人微振盪**：模型輸出高頻方向翻轉（~60% cycles 有 sign change），幅度 <1° 所以 guard PASS，但肉眼抖動明顯。這不是 guard pipeline 問題，是模型輸出問題。可能需要 action smoothing / EMA filter。
3. **MCAP 回讀 Risk Log**：用戶提到想從 MCAP 讀取歷史 risk data，尚未實作。

## 當前 blocker

無

## 會話記錄

### Session 2026-05-26 #2 (Deep DX Audit + README Rewrite + LinkedIn)

- **本輪目標**: 深度 DX 審查、修正架構問題、重寫 README、撰寫 LinkedIn
- **已完成**:
  - 4 agent 平行審查: Python API onboarding / 架構隱藏耦合 / 前端 DX / 測試安全
  - `GuardResult.pass_result()` alias（與 `GuardDecision.PASS` 對齊命名）
  - `Observation.merged()` factory 方法，消除 `guard_runtime.py` 中 6 處 `object.__setattr__` 突破
  - `examples/hello_guard.py` — 最小 guard 範例
  - `examples/custom_callback.py` — 自定義 boundary callback 範例
  - `examples/stackfiles/minimal.yaml` — 最小合法 Stackfile
  - README 完整重寫：problem-first 敘事、inline code、ASCII pipeline、誠實 disclaimer
  - `OODTrainer.tsx` 4 處硬編 `fetch()` → 集中化 `api.ts`
  - `hardware.py` / `ood.py` silent exception → `logger.debug()`
  - LinkedIn 貼文草稿（兩版：長版 problem-first / 短版 direct）
  - Spawn 3 個獨立改善任務
- **執行過的驗證**:
  - `.venv/bin/python -m pytest tests/unit/ tests/safety/ -x -q` — 668 passed
  - `ruff check dam/` — all checks passed
  - `cd dam-console && npx tsc --noEmit --skipLibCheck` — no errors
  - `cd dam-console && npm test -- --ci` — 109 passed
  - `python examples/hello_guard.py` — PASS/CLAMP 正確
  - `python examples/custom_callback.py` — PASS/CLAMP 正確
  - `dam validate examples/stackfiles/*.yaml` — 5/5 valid
- **commits**: `046a532`, `b14b327`

### Session 2026-05-26 #1 (Developer Experience Audit)

- **本輪目標**: 以開發者角度全面審查專案，修復會讓開發者困惑的設計，強化值得驚嘆的亮點
- **已完成**:
  - 4 agent 平行審查: Python API DX / 架構命名 / 前端 DX / 測試安全
  - `GuardResult.reject()` 參數順序統一為 `(guard_name, layer, reason)`
  - 移除 `@guard` 死參數 `_process_group`
  - 清理 `builtin_callbacks.py __all__` 私有符號
  - `Guard.check()` 加 injection kwargs docstring
  - 前端 `gGuardMap` 型別安全化 `Record<string, GuardStatus>`
  - `GuardDecision` 加入 `STANDBY`，EventLog 改用 `RuntimeDecision`
  - `runtime/contexts.py` → `runtime/builtin_contexts.py`
  - Spawn 3 個獨立改善任務（property testing / shim 消除 / safety markers）
- **執行過的驗證**:
  - `.venv/bin/python -m pytest tests/unit/ tests/safety/ -x -q` — 668 passed
  - `make lint` — all checks passed
  - `cd dam-console && npx tsc --noEmit --skipLibCheck` — no errors
  - `cd dam-console && npm test -- --ci` — 109 passed
- **commit**: `de3aca6`

### Session 2026-05-25 #6 (L3 Hardware Guard Redesign)

- **本輪目標**: 將 hardware_watchdog 拆成多個獨立 callback，加入 category 分組機制，統一 fallback 場景設計，移除死代碼
- **已完成**:
  - `@boundary_callback` decorator 新增 `category` 欄位，存入 catalog 及 fn attr
  - 所有 callback 標記 category: hardware/host/kinematics/execution/anomaly
  - Catalog API 支援 `?group_by=category` 分組查詢
  - `event_class` 加入 telemetry guard_statuses 序列化（含 bugfix: 缺少 `()`）
  - `hardware_snapshot` 改為 flat 結構（top-level temperatures/currents/voltages + host_health）
  - HardwarePanel 直讀 flat data，移除 MetaTree/ScalarGrid/isHostHealthGuardName 死代碼
  - CycleSafetyInspector 新增 `GroupedGuardCards` 按 event_class 分組顯示
  - 移除 `hardware_status` injection pool key、StepContext field、HardwareGuard param
  - 所有 stackfile 拆分 L3 boundaries: temperature→slow_down, current→hold_position, voltage→emergency_stop, host→slow_down
  - 修正 so101_qp.yaml: temperature/current params 原錯放在 hardware_watchdog callback
- **執行過的驗證**:
  - `.venv/bin/python -m pytest tests/unit/ -x -q` — 585 passed
  - `make lint` — all checks passed
  - `cd dam-console && npx tsc --noEmit --skipLibCheck` — no errors
  - `cd dam-console && npm test -- --ci` — 100 passed
  - `cd dam-console && npm run build` — passed
  - `.venv/bin/dam validate examples/stackfiles/*.yaml` — 5/5 valid
  - `.venv/bin/python scripts/check_docs.py` — passed
- **commits**: `7a1e889`, `35457ff`, `3cbf2c0`, `64aca96`

### Session 2026-05-25 #5 (Hardware Telemetry Separation)

- **本輪目標**: 將 hardware telemetry 從 guard decision path 分離——guard 只負責判斷，數據走觀測層
- **已完成**:
  - `CycleResult` 新增 `hardware_snapshot` field，由 runtime 從 `obs.metadata` 組裝
  - `_serialise_cycle()` 從 `result.hardware_snapshot` 直接讀取，刪除 ~100 行 guard metadata mining 代碼
  - `HardwareGuard` 移除 `_extract_telemetry()` / `_telemetry_summary()` / `_collect_host_health_if_active()`
  - `host_health_limit` callback 改從 `obs.metadata["host_health"]` 讀取（與其他 L3 callback 一致）
  - Runtime 在觀測階段注入 `collect_host_health()` 結果到 `obs.metadata["host_health"]`（built-in source）
  - PASS path 的 GuardResult 不再攜帶 telemetry metadata
- **執行過的驗證**:
  - `python -m pytest tests/unit/ -x -q` — 550 passed, 21 skipped
  - `make lint` — all checks passed
- **未完成的清理（已 spawn_task）**:
  - `hardware_status` injection pool key 可移除——沒有 callback 消費它
- **commit**: c870cd2

### Session 2026-05-25 #4 (Unified L0 OOD Boundary Authoring)

- **本輪目標**: 將 Guard 編輯器中四個近似 L0 OOD callback 收斂成一個可理解的設定入口
- **已完成**:
  - L0 OOD boundary 只暴露 `ood_detector`；三種算法改為 `backend` 參數，不再註冊為額外 callback
  - `ood_detector` 透傳 `normalizing_flow`/`memory_bank`/`welford` 所需參數，包含 vision fusion、`nll_sigma` 與 `z_threshold`
  - 修正 Real-NVP 只提供 model/flow bundle、沒有 memory bank 時統一入口不載入模型的問題
  - 前端以 backend selector 切換對應參數欄位；新建與訓練預設使用 Real-NVP
  - RQ1 runtime bundle、範例 Stackfile 與 L0 文件統一改用 `callback: ood_detector` + `backend`
- **執行過的驗證**:
  - `.venv/bin/python -m pytest tests/unit/ -x -q` — 580 passed
  - `.venv/bin/python -m pytest tests/unit/test_ood_callbacks.py tests/unit/test_builtin_callbacks.py tests/safety/test_ood_regression.py -q` — 66 passed
  - `cd dam-console && npm test -- --runInBand` — 100 passed
  - `cd dam-console && npm run build` — passed（保留既有 Turbopack NFT warning）
  - `.venv/bin/dam validate examples/stackfiles/demo.yaml examples/stackfiles/test.yaml examples/stackfiles/so101_qp.yaml` — 3/3 valid
  - `.venv/bin/python scripts/check_docs.py` — passed
- **介面決策**:
  - 不保留 `ood_welford` / `ood_memory_bank` / `ood_normalizing_flow` Stackfile callback；既有檔案需改成 `ood_detector` 並設定 `backend`

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
