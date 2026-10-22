# claude-progress.md — 進度日誌

## 2026-10-22 — L3/R07 accepted; L4 environment audit

- L2 committed as c20955e. L3 exposes reload receipts backed by the same manager as Console, session status, reset and task switch. Pending is not applied; structural/stale updates reject without changing the active configuration.
- Reset closes old resources before replacement, clears per-session state, preserves last applied version, monotonic cycle ordering and bounded receipts, and cancels pending updates. Cleanup/rebuild failure blocks evaluation until recovery; retries preserve configuration and receipt evidence.
- Verification: full non-hardware Python 1317 passed in 29.14s; focused session 11 passed; mypy/Ruff/format passed; offline session example and docs-check passed. Full pre-commit rerun before commit (prior hook detected concurrent documentation edits despite no mypy errors).
- L4 audit: host macOS arm64 with Python 3.12, uv and Docker available; Docker daemon Linux aarch64. Python 3.11 and Linux x86_64 execution remain to verify. No hardware/automations. L4–L6 incomplete.

## 2026-10-22 — L2/R06 accepted; L3 next

- L1 committed as e31e837. L2 adds mapping/Path/StackfileConfig construction and public static preflight reports with field-level issues, callback requirements, solver reference/factory, command layout/units and fallback checks.
- Preflight tests prove no runtime, solver, user callback/fallback constructor, worker or recorder creation. It reads configuration/presets only; unspecified command units are explicit warnings, not inferred. Construction copies caller configuration.
- Verification: 1306 full non-hardware Python passed in 39.47s; final focused preflight 19 passed after a mypy-only variable rename; full pre-commit and docs-check passed; offline public example passed. Evidence: docs/acceptance-l2.md and logs/acceptance-l2-*.
- Continue L3 public reload receipts, task switching and reset semantics. L3–L6 incomplete; no hardware or automations activated.

## 2026-10-22 — L1/R05 accepted; L2 next

- S1–S3 committed as 03e3e16. L1 adds public Guardrail.evaluate / GuardrailEvaluation, detached per-call evidence, decision/fallback/config version/cycle/consumed verdict age, and JSON serialization. SDK reports returned, never device-sent.
- Public DamError categories distinguish configuration, input, closed session, timeout and execution failures with original cause chains. Guard rejection/FAULT remain results with fallback. Callable retains command-only return.
- L1 verification: Python full non-hardware suite 1287 passed in 34.69s; targeted earlier 86 passed; full pre-commit passed; docs-check passed; executable offline example passed. Reports: docs/acceptance-l1.md; logs/acceptance-l1-*.
- No Console/Rust changes in L1, no hardware/automation. Continue L2 mapping/config construction and static no-resource preflight. L2–L6 incomplete.

## 2026-10-22 — S1–S3 short-term acceptance complete

- S1, S2 and S3 accepted; reports in docs/acceptance-s1.md, docs/acceptance-s2.md and docs/acceptance-s3.md. Full plan remains incomplete: L1–L6 next.
- Same candidate: Python 1276 passed; Rust workspace exit 0 (one existing ignored doc-test explicitly excluded); frontend 20 suites/167 passed; production build and TypeScript passed; full pre-commit including mypy and Rust clippy passed; new untracked Python Ruff/format passed; docs-check and diff whitespace passed. 384 source SHA-256 fingerprints unchanged across final verification.
- Jest async/duplicate-package warnings fixed. Remaining build warnings: stale Browserslist data and dynamic upload-asset NFT tracing; standalone packaging/platform matrix remains L4 scope.
- No services or hardware running from acceptance work, no automations resumed. Evidence logs under logs/acceptance-s*. Next: L1/R05 structured public results and classified errors.

## 2026-10-22 — S2 accepted; S3 candidate verification in progress

- Final fixed-source Python suite: 1276 passed in 71.08s, exit 0; logs/acceptance-s2-python-final.log. Source hashes: logs/acceptance-candidate-source.json. Earlier failing/intermediate runs are not final evidence.
- Completed R04 output/fallback paths, including explicit SDK command-space fallback; Hold/Retreat/SlowDown reject non-joint layouts; Retreat missing/invalid prerequisites fault instead of returning to normal or inventing zero targets; SlowDown without history suppresses output and rejects invalid scale.
- S1 and S2 reports: docs/acceptance-s1.md, docs/acceptance-s2.md. Plan marks S1/S2 complete, S3 in progress. Rust workspace tests passed; docs-check passed before final report update. Full pre-commit rerun is active, frontend full tests/build follow sequentially.
- Prior full-suite Watchdog and frontend timeout failures occurred under heavy host load. Deadlines/assertions were not relaxed; fixed-source Python suite now passes all tests.
- No hardware connected, no automation resumed. Changes remain uncommitted. L1–L6 are not complete; continue S3 then L1 per plan.

## 2026-10-22 — S2 R04 implementation, full acceptance in progress

- Reproduced NaN commands reaching fake sinks in enforce/monitor/log_only. Implemented shared proposal/final payload validation and declared command dimension checks; layout takes priority over observation joints. Moved existing segment-size resolver to shared action types.
- Final fallback output is checked before dispatch. Policy/context/fallback/sink errors are retained in cycle results; sink failure does not commit command history. Added output_status to CycleResult and telemetry. Physical emergency-stop errors remain visible with a terminal no-send context.
- Context entry runs immediately but new context step is deferred until next cycle to prevent callback duplication. SlowDown preserves timestamp and gripper. Documentation updated with data validity versus safety-enforcement semantics and transport acceptance limits.
- Added 52 R04 tests (latest count pending final verification). Targeted earlier runs: 40, then 48 R04 cases passed; 87 focused monitor/guard/R04/watchdog tests passed; 62 engine/output cases passed before final shape-snapshot refinement. Do not sum repeated runs.
- Initial full run: 1146 passed / 5 failed before maxfail. Three old monitor tests used NaN to represent a safety violation; changed them to finite disallowed commands, preserving monitor pass-through assertions. Fixed an over-broad audit-original validation that rejected legitimate standalone gripper corrections. Watchdog ping timing failed under heavy host load but passed isolated; deadline unchanged.
- Full Python validation and pre-commit are running; Rust workspace build/test is running. No S2/S3 completion claim yet. Evidence logs: logs/acceptance-s2-python.log, logs/acceptance-s3-precommit.log. Redundant standalone full mypy was cancelled; pre-commit runs the same check. No changes committed.

## 2026-10-22 — S1 completed; continuing to S2

- Executed isolated browser/API acceptance using disposable files and offline runtime, with no sink or host-control routes. Added reproducible `tests/acceptance/safety_server.py`.
- Browser found nullable API declarations rejected by local-draft restore. Aligned TypeScript/validator with backend null fields; added regression. Browser refresh now restores the original pending JSON correctly.
- Browser verified saved plan, compile/export, duplicate refusal, hot reload and rollback receipts, real MCAP A/B (5 cycles / 2 differences), regression PASS and deliberate FAIL (2 mismatches). See `docs/acceptance-s1.md` for item-by-item evidence and local screenshots.
- Verification: baseline 1102 unit; targeted backend/helper 180; frontend 167 / 20 suites; production build and TypeScript passed. Temporary servers stopped and temporary data removed; evidence retained in logs/acceptance-s1.
- S1 accepted using combined browser, API and component/fault-injection tests. S2 R04 begins next; overall plan remains incomplete. Changes are not yet committed.

## 2026-10-22 — Acceptance and delivery plan established

- User requested short- and long-term acceptance criteria and deliverables through completion. Added `docs/acceptance-plan.md` and linked it from roadmap and documentation navigation.
- Short term: S1 isolated browser/API workflow acceptance, S2 R04 final-output/fallback contract, S3 complete candidate validation. Long term: L1–L6 map to R05–R10 with public SDK, preflight, lifecycle, install matrix, diagnostics/soak and executable examples.
- Defined evidence requirements, defect gates, platform and numerical performance/resource targets, and distinct offline-software versus device-deployment completion. Targets are prospective, not achieved claims; untested platforms remain unverified.
- Planning artifact is complete; implementation milestones remain pending as shown in the plan. Next execution item is S1 browser/API acceptance using temporary data and fake runtime/sink. Automation remains paused; no hardware actuation is authorized by this plan.
- Validation: docs-check (strict documentation build) passed. No runtime code changed in this planning unit; prior test counts are retained only as dated baseline evidence.

## 2026-10-22 — Safety Studio offline acceptance follow-up

- User lifted the execution restriction and authorized choosing the next work item. Resumed Safety Studio hardening follow-up; hourly automation remains paused.
- Checkout started clean at `b238cff`; the previous checkpoint changes were already committed. This round changes only the historical switch label and documentation, with no runtime behavior changes.
- Replaced misleading historical "Active but not saved" with "Applied change was not saved". Reviewed receipt retention and superseded-configuration persistence paths against existing storage/lifecycle regressions.
- Documented local draft restoration, incomplete edits, storage failure/retry and damaged-draft replacement behavior; fresh compile/preview/frame confirmation requirements; all-or-nothing language drafting; strict regression range coverage and malformed-case isolation.
- Fresh verification: Python unit suite 1102 passed; integration/safety/property suite 91 passed (hardware excluded); frontend 166 tests / 20 suites passed, including previously unrun replay and editor tests. Production build (after label edit) and its TypeScript check passed. Targeted mypy on six safety service/router files, targeted Ruff, docs-check and git diff --check passed. Frontend tests preceded the text-only label edit; no behavior was changed afterward.
- Offline demo: 5 cycles, 2 differences, regression pass (2 assertions). No sample installation or library overwrite.
- Remaining limits: no new browser end-to-end or real-hardware verification, no Rust verification this round. Build retains upload-asset/NFT tracing and outdated Browserslist warnings; Jest reports generated standalone package-name collision and an existing async act warning despite passing. No claim of hardware safety or complete end-to-end acceptance.
- Changes remain uncommitted. R04 final-output/fallback contract remains a separate next work item.

## 2026-10-16 — Safety Studio hardening checkpoint (stopped at user request)

- User requested staged wrap-up and stop. All three delegated agents have stopped; no further feature work or new validation is running. Changes remain uncommitted in the shared workspace; unrelated work is preserved.
- Implemented local draft persistence/restoration including incomplete boundary JSON and raw declarations, storage failure/corruption messages, stale async-response guards, and keep-out sphere add/remove. Compile results, switch tokens and frame confirmation are not restored.
- Authoring now rejects proven no-op/degenerate geometry, malformed action/joint declarations, boolean joint bounds, non-finite original values, ignored degree flags, and conflicts after degree normalization. Partially unresolved language requests produce no actionable component.
- Switching now reports saved/active configuration read/validation errors while retaining acknowledged receipts. Applied changes superseded by another runtime/configuration are not written back to disk. Hashing rejects non-finite values before Pydantic JSON serialization can turn them into null.
- Replay cases require complete requested range/assertion coverage; malformed stored cases are isolated and reported, ID/filename consistency is checked, and UI refresh/save races and non-JSON errors are handled.
- Verification completed this round: initial unit baseline 1052 passed; root switching tests 30 passed (11 new); delegated authoring tests 95 passed (34 new), targeted ruff and mypy clean; delegated replay Python tests 23 passed, ruff/format clean; Console targeted Jest 18 passed and production-source TypeScript passed. Replay original 2 UI tests and source TypeScript passed before 6 additional tests were added; those 6 new tests were NOT run.
- This checkpoint is not a full integration acceptance. No full post-change suite, production build, browser verification or hardware verification was performed. Root switching independent QA, latest replay Python mypy, final docs-check and full cross-component review remain pending because of the stop request. Do not reuse the previous round's 1143/152 counts as validation of this checkpoint.
- Known review follow-up: historical applied-but-superseded rows retain a truthful backend message, but the UI's generic "Active but not saved" badge may need wording adjusted to avoid implying current activation. No edit was made after identifying this during wrap-up.
- Documentation: switching failure behavior updated in docs/safety-studio.md; remaining authoring/draft/replay documentation alignment is pending. Resume from this checkpoint only on user request.

## 2026-10-16 — Safety Studio and safety plan workflow

- Implemented all six requested core workflows: additive baseline/scene/task/mode plans, static wiring diagnostics and conflict checks, reviewed configuration switching with runtime receipts/rollback, complete-cycle A/B replay plus executable regression cases, projected 3D workspace/keep-out editing, and bounded Chinese/English quantitative rule drafts.
- New entry: Console sidebar → Safety Studio (`/safety`); Comparison contains Safety A/B comparison & regression cases. Compiled plans can be saved exclusively to the existing Stackfile library. Sample saved plan: `demo-precision`; sample Stackfiles: `comparison-roomy`, `comparison-precise`; sample MCAP: `session_safety_comparison_demo.mcap`. No existing library names were overwritten.
- Switch previews bind active hash/version/task/lifecycle epoch and saved-file identity. Hot reload receives a control-cycle acknowledgement before persistence; failures retain old configuration. Stop/start invalidates queued and preparing changes, with commit-time checks. Structural changes save only while idle/stopped and require the existing reload/start workflow. Baseline boundaries cannot be removed or changed through a switch.
- Replay evaluates every available cycle, retains FAULT, rejects missing history, snapshots configs and recording hashes, and isolates acceleration callback caches from live runtime and other candidates. Demo: 5 compared cycles, 2 differences, regression PASS. No hardware actuation was performed.
- Validation: current checkout Python unit/integration/safety/property suite **1143 passed**; frontend **152 tests / 19 suites passed**; production Next.js build and TypeScript passed; targeted Python mypy/ruff/format and `make docs-check` passed. Production build retains a pre-existing upload-asset/NFT tracing warning. Concurrent unrelated workspace changes were preserved and not reverted or committed by this task.
- Browser verification: imported example, compiled declarations, generated a Chinese quantitative rule, composed mode/scene, inspected 3D editor and confirmed missing FK disables preview. A subsequent browser session could not fetch cross-origin API data despite reachable API; A/B and regression behavior was verified through tests and the executable demo. Temporary verification servers on 13000/18080 were stopped.
- Limits: authoring checks declarations, not sensor health. Native frame must be declared; moving frames/transforms and spatial speed-zone callbacks are not implemented. Language drafts use an explicit local grammar, leaving ambiguous/conditional clauses unresolved. Replay cannot reconstruct phase transitions, absent images/host health/watchdog age or arbitrary custom callback history; slow guards run synchronously offline with timing limitations displayed. No real-robot verification.
- Guides: `docs/safety-studio.md`, `docs/safety-comparison.md`; reusable example: `examples/safety_plans/precision.json`; runnable demo: `.venv/bin/python -m examples.safety_comparison_demo`.


## 2026-10-16 — R03 slow-lane ownership and generation isolation

- Completed `6effe45`: detached input/result buffers, submission-time runtime context, worker-owned guards/solvers, and task/configuration/state-generation verdict checks.
- Slow stages execute sequentially so successful worker shutdown includes callback completion. Copy failures become FAULT; caller wait deadlines remain bounded, but copying and callback shutdown have separate costs.
- Validation in an isolated checkout based on `69d5f1f`: 1007 Python tests, mypy (222 source files), Ruff, pre-commit and strict docs build passed. Image-copy measurements and platform limitations are recorded in `docs/roadmap.md`.
- The hourly DAM automation was paused at the user's request. Next roadmap item: R04 final-output/fallback contract. Other chats' uncommitted editor, service and runtime changes were preserved and were not included in this verification.


## Session 2026-10-16 — Static review of recording and risk-log workflows

- 延續使用者禁止編譯／執行的限制；只做原始碼閱讀、檔案修改、人工差異審查與 `git diff --check`。
- MCAP 刪除 UI 改為與現有後端一致的永久刪除提示；批次部分成功時移除成功項目並保留失敗項目供重試。切換錄製檔重設刪除確認，舊刪除回應不清除新選取的檔案。移除 session card 的巢狀互動元素。
- 切換錄製檔立即替換週期資料；手動切換清除上一份檔案的 cycle query，保留初始深連結。增量讀取正確處理 cycle ID 0；選取項目從列表消失時不再解參照不存在的 summary。
- 相機播放器依錄製檔／相機清單／live mode 隔離狀態，忽略過期 frame-list 回應並避免重複請求；初次顯示依指定時間定位。live image 保持 img 掛載並讀取既有快取，修正首幀遺失及過早 revoke。
- Risk Log 忽略舊篩選／來源的過期回應，避免清除時被先前請求回填，cycle 0 深連結可凍結並展開詳細資料。
- 新增 11 個前端回歸案例（McapCameraPlayer、McapSessionList、McapViewer、RiskLogTable），均未執行；不得視為測試通過。其他既有修改與 replay comparison 進行中的工作保持原樣。

## Session 2026-10-16 — Service startup and test reporting (static only)

- 使用者確認只做靜態修改與審查；本輪未執行測試、編譯、linter 或服務。
- dev/run 共用新的 service lifecycle helper：readiness 後才顯示 ready、服務異常退出回報、獨立日誌、只清理本次啟動的 process groups，關閉有 10 秒等待上限。
- 修正未安裝 NVM 時的 pipeline 提前退出；test helper 缺依賴不再顯示成功，Rust/frontend-only 不要求 Python venv。
- 已加入隔離服務與測試入口回歸案例，尚未執行；只完成人工檢閱與 `git diff --check`。本次驗證狀態不得沿用上一輪的 918 passed。


## Session 2026-10-16 — Static user-flow review of Stackfile editors

- 依使用者要求僅閱讀、修改與比對檔案；未編譯、啟動服務、執行專案程式、測試或 linter。
- 修正 Config/Guard 在初次載入完成前自動儲存預設內容、載入既有 YAML 後立即重寫，以及切換空設定時沿用上一份規則的問題。未編輯時保留原始 YAML；初次讀取失敗顯示錯誤並提供 Retry load。
- Stackfile New 改用 exclusive-create API，重名回傳 409 且不覆寫；拒絕非字串／空白 YAML。刪除前先讀取 Live 替代內容，避免讀取失敗後停留在已刪除項目。
- YAML parser 正確保留明確的空 boundaries/tasks/fallbacks，跳過獨立的 `{}` 空 mapping。
- 已新增 API、hook 與頁面回歸案例；本輪只有人工靜態審查及 `git diff --check`，測試結果待使用者允許執行後確認。保留其他進行中的 runtime/safety 等修改，未提交。

## Session 2026-10-16 — Local workflow friction fixes

- Make 的 Python 工具改用目前 interpreter 的 module 入口，避免移動 checkout 後舊 console-script 路徑失效；`init.sh` 從自身位置定位專案並使用專案 Python。
- 移除 dev/run 對佔用 ports 程序的強制終止；新增非破壞性 port preflight，production frontend 缺 build 時在啟動 backend 前停止。
- `dam inspect` 顯示 enforcement mode、always_active 以及各 task 合併後的 configured boundaries。
- 舊 LLM prompt 收斂為 skill 入口；修正 CLI 套件名、單位來源、pipeline 敘述與排查流程。mobile-base 範例使用 context manager、檔案相對路徑及最嚴格 decision 顯示。
- 驗證：33 targeted tests；最終 unit suite 918 passed；9/9 Stackfiles、docs-check、ruff、shell syntax、skill validator 與離線 mobile-base 範例通過。無實機／服務啟動驗證。
- 初始 baseline 的 replay/QP 測試曾失敗，其他進行中的變更更新後全套通過；本輪未修改 guard/QP 或 replay test。

## Session 2026-10-16 — DAM agent skill

- 新增 `skills/dam-agent/`，提供 Stackfile、Guardrail 整合、核心維護與唯讀事故排查流程，以及按需讀取的原始碼路由和離線範例。
- README 加入 skill 入口與安裝方式；本機 `~/.codex/skills/dam-agent` 以 symlink 指向專案內版本。
- 驗證：skill validator 通過；離線 PASS/CLAMP 範例與 reference paths 通過；minimal Stackfile validate/inspect 通過；`make docs-check` 通過；本輪開始時 unit baseline 為 886 passed。
- 環境限制：`.venv/bin/dam` 指向已不存在的舊 interpreter 路徑；本輪使用 `.venv/bin/python -m dam.cli` 完成驗證，未重建環境。未執行實機操作。

## 當前已驗證狀態

- **專案**: DAM (Detachable Action Monitor) v0.5.0
- **倉庫根目錄**: `/Users/chenyizhong/Documents/Claude/Projects/Security Guard.nosync`
- **標準啟動路徑**: `make dev`
- **標準驗證路徑**: `make test`
- **基線狀態**: `make test` — all checks passed（截至 2026-06-09）；755 unit + 28 integration + 35 safety + 2 property + Rust + Jest 109

## 當前最高優先級未完成功能

1. **Guard input_space 雙空間支援**：🟢 **schema + SafetyGuard resolver-backed EE path 已完成；runtime/sink 仍保持 joint contract**。見 session-handoff.md。
2. **robot-dam pip 打包 + Isaac Sim 整合**：🟡 **Isaac joint + resolver-backed EE wrapper 已收斂，safety import 已從 IsaacLab eager imports 解耦；下一步是 IsaacLab runtime launch 驗證與極小 EE snippet**。
3. **Vision OOD 閾值校準**：✅ **已實作 percentile-based threshold**（`threshold_percentile` param）。需在實際 dataset 上重新 calibrate 以驗證 FPR 改善。
4. **機器人爆衝防護**：✅ **已拆分為獨立 boundary**。`joint_velocity_limit`（馬達安全）+ `joint_acceleration_limit`（smoothing）+ `ee_velocity_limit`（環境安全）。
5. **MCAP 回讀 Risk Log**：✅ **已實現**。Backend: `/api/risk-log/mcap/{filename}`，Frontend: MCAP viewer page with cycle detail expand。
6. **`make record` 端到端硬體驗證**：需要實機環境。

## 當前 blocker

無

## 會話記錄

### Session 2026-06-08 #7 (Code review + safety hardening)

- **本輪目標**: 全面 code review 和 safety hardening
- **已完成**:
  - 移除 deprecated `check_force_torque_safe` / `force_limit` shims 和全 re-export chain
  - 修復 EE resolver path：NaN/Inf propagation、resolver exception handling、dimension check 前置
  - 修復 `joint_velocity_limit` / `joint_acceleration_limit` 在 `derived=False` 時不更新 position 的 bug
  - 移除 stale back-compat comments（`fallback_triggered`、`guard_runtime.py`）
  - 新增 24 項測試（18 FK sanity + 5 EE failure mode + 1 velocity position consistency）
- **執行過的驗證**:
  - `make test` — all checks passed（752 unit + 28 integration + 35 safety + 2 property + Rust + Jest）
  - `pytest -W error` — 0 warnings
- **未修的 review findings（已記錄為 spawn_task）**:
  - ~~`joint_acceleration_limit` 無 QPTerm~~ → ✅ 已修
  - ~~`_prev_vel` 全域狀態需 scoping 到 pipeline lifecycle~~ → ✅ 已修
- **下一步**:
  - 極小 EE-policy snippet（resolver path end-to-end）
  - 或等 Isaac runtime 驗證 body frame 對齊

### Session 2026-06-09 #8 (Architecture debt: accel callback)

- **本輪目標**: 消除 `joint_acceleration_limit` 三項架構債
- **已完成**:
  1. **QPTerm metadata**: 加速度 clamp 結果現在帶 position box bounds（從 acceleration-limited velocity range 推導），QP aggregator 可以 enforce
  2. **Timestamp-based staleness**: `_prev_vel` 帶 `time.monotonic()` 戳記，elapsed > `dt * 5` 時自動 reset baseline，防止 pipeline restart 時 stale velocity history 造成 false alarm
  3. **Instance-scoped state**: pipeline runner 注入 `boundary_name` 到 callback kwargs，`joint_acceleration_limit` 用它當 `_prev_vel` key（取代 hardcoded string），多個 boundary container 用同一 callback 不會互相汙染
- **新增測試**: 3 項（staleness reset、QPTerm 驗證、boundary_name scope 隔離）
- **執行過的驗證**:
  - `make test` — all checks passed（755 unit + 28 integration + 35 safety + 2 property + Rust + Jest 109）
- **後續追加**:
  4. **ee_velocity_limit QPTerm**: 線性化 `||J @ v|| <= max_v` 為 A-matrix QPTerm，QP aggregator 可 fuse
  5. **unused imports cleanup**: ruff F401 清理 19 項未用 import
  6. 所有 L1 kinematics callbacks 現在都產生 QP metadata
- **執行過的驗證**:
  - `make test` — all checks passed（756 unit + 28 integration + 35 safety + 2 property + Rust + Jest 109）
- **下一步**:
  - 推進 EE-policy snippet / Isaac runtime 驗證
  - 或其他品質改進

### Session 2026-06-08 #6 (Pinocchio FK frame sanity test)

- **本輪目標**: 驗證 Pinocchio FK 在 SO-ARM-101 URDF 上的 frame convention 正確性
- **已完成**:
  - 新增 `tests/unit/test_pinocchio_fk_sanity.py` — 18 項測試
  - 驗證 reduced model 結構（5 DOF、gripper locked）
  - 驗證 home pose workspace 合理（0.375m，Z > 0）
  - 驗證 quaternion convention 為 DAM [qx,qy,qz,qw]
  - 驗證 workspace 在物理臂展範圍內（多組 joint config）
  - 驗證 wrist roll 只影響 orientation 不影響 position
  - 交叉驗證 Isaac sidecar `_PinocchioForwardKinematics` 使用相同 URDF 產生相同結果
- **執行過的驗證**:
  - `pytest tests/unit/test_pinocchio_fk_sanity.py -x -v` — 18 passed
  - `pytest tests/unit/ -x -q` — 750 passed（baseline 不破）
- **結論**:
  - FK convention 正確：meters、[qx,qy,qz,qw]、合理 workspace
  - Isaac resolver adapter 的 FK path 可信任
- **下一步**:
  - 在有 IsaacLab runtime 的環境中驗證 Isaac body frame 與 Pinocchio FK frame 對齊
  - 或做極小 EE-policy snippet 使用 resolver path

### Session 2026-06-08 #5 (Isaac sidecar lazy safety import)

- **本輪目標**: 在無 IsaacLab shell 中繼續推進 Isaac 整合品質，修掉 safety wrapper 測試需要手動 module loader 的 import smell
- **已完成**:
  - `/tmp/isaac_lab_study/tools/controll_scripts/__init__.py` 改為 lazy top-level exports
  - 保留 `from controll_scripts import ControllerFactory, SOArm101Config, ...` 的現有 API 形狀
  - `from controll_scripts.safety import DAMSafetyWrapper` 不再因 parent package eager import Isaac controllers 而失敗
  - `tests/test_dam_safety_wrapper.py` 移除手動 `sys.modules` / `importlib.util` loader，改回正常 package import
- **執行過的驗證**:
  - Isaac runtime discovery: `isaaclab` / `isaacsim` command not found；Python `isaaclab` / `isaacsim` missing；`torch` / `pinocchio` found
  - `/tmp/isaac_lab_study`: `python -m py_compile tools/controll_scripts/__init__.py tests/test_dam_safety_wrapper.py` — passed
  - `/tmp/isaac_lab_study`: `PYTHONPATH=tools /Users/chenyizhong/Documents/Claude/Projects/Security\ Guard.nosync/.venv/bin/python -m pytest tests/test_dam_safety_wrapper.py -q` — 3 passed
- **未驗證**:
  - IsaacLab full launch 仍未跑；目前 shell environment 沒有 `isaaclab` runtime
- **下一步**:
  - 在有 IsaacLab runtime 的 shell/環境中跑 `scripts/dam_safety_demo.py --controller ik`
  - 若仍無 runtime，下一個可做的非 runtime 單元是 Pinocchio FK / SO-ARM URDF frame sanity test

### Session 2026-06-08 #4 (Isaac resolver-backed EE wrapper)

- **本輪目標**: 在 `/tmp/isaac_lab_study` 做 concrete Isaac resolver adapter，消除 demo 內重複 IK 解算，讓 DAM `SafetyGuard(input_space="ee")` 能被 Isaac loop 真正使用
- **已完成**:
  - 新增 `tools/controll_scripts/safety/isaac_resolver.py`
  - `IsaacControllerKinematicsResolver` 使用 live Isaac `Articulation` + 既有 controller IK 做 EE pose → joint proposal
  - 使用 bundled SO-ARM-101 URDF + Pinocchio 做 validated joint target → safe EE pose FK；不偽造 FK 回傳
  - `DAMSafetyWrapper.attach_isaac_controller()` 啟用 EE resolver-backed guard
  - `DAMSafetyWrapper.filter_ee()` 對外仍回傳 Isaac 要套用的 safe arm joint targets，並保存 DAM validated gripper target
  - 修正 joint/EE demo 之前「gripper 經過 DAM 但套用 raw command」的不一致，改用 `last_safe_gripper`
  - 刪除 `scripts/dam_safety_demo.py` 與 `scripts/dam_teleoperate_demo.py` 內重複 IK 解算邏輯
- **執行過的驗證**:
  - 主 DAM repo baseline: `.venv/bin/python -m pytest tests/unit/ -x -q` — 732 passed
  - `/tmp/isaac_lab_study`: `python -m py_compile scripts/dam_safety_demo.py scripts/dam_teleoperate_demo.py tools/controll_scripts/safety/dam_wrapper.py tools/controll_scripts/safety/isaac_resolver.py tools/controll_scripts/safety/__init__.py tests/test_dam_safety_wrapper.py` — passed
  - `/tmp/isaac_lab_study`: `PYTHONPATH=tools /Users/chenyizhong/Documents/Claude/Projects/Security\ Guard.nosync/.venv/bin/python -m pytest tests/test_dam_safety_wrapper.py -q` — 3 passed
- **未驗證**:
  - IsaacLab full launch 未跑；目前 shell environment 沒有 `isaaclab` runtime
- **下一步**:
  - 在 IsaacLab runtime 中跑 `scripts/dam_safety_demo.py --controller ik`，驗證 Pinocchio FK frame / Isaac body frame 對齊
  - 若 launch 通過，再補極小 EE-policy snippet；不要提前改 DAM runtime/sink contract

### Session 2026-06-08 #2 (input_space contract + Isaac joint cleanup)

- **本輪目標**: 以最高標準收斂 input_space 第一交付單元，避免過度設計與設計轉移
- **已完成**:
  - `HardwareConfig.input_space` / `PolicyConfig.input_space` schema 欄位與 validation（`joint` / `ee`）
  - `StackfileConfig` validation：有 `hardware` + `policy` 時兩邊 `input_space` 必須一致
  - `SafetyGuard(..., input_space=...)` 讀 stackfile 預設並允許 override
  - `SafetyGuard` joint path 保持既有行為；`ee` path 在缺 IK/FK resolver 時明確報錯，避免把 EE pose 靜默當 joint action
  - `SafetyGuard.set_ee_pose()` 保留為 current EE observation injection，不作為 target EE action
  - 修復 `make test` 基線問題：dataset hardware replay unit test mock Rust frame hub；vision model tests gated on optional torch/torchvision；`SafetyGuard.__del__` 初始化失敗不再噴 warning
  - 更新 `docs/quick-stack.md`
  - `/tmp/isaac_lab_study` demo 收斂為已落地的 joint-target DAM filter，移除 EE demo / `input_space` future key / OSC 假入口
- **/review 結論**:
  - 不新增 `EEActionProposal` / `ValidatedEEAction`；使用現有 `ActionProposal.target_ee_pose`
  - 不在 runtime/callbacks 分散 FK/IK；下一步先做 API-level resolver protocol
  - 不在 Isaac sidecar 提前寫未落地 EE snippet
- **執行過的驗證**:
  - `python -m pytest tests/unit/ -x -q` — 689 passed, 40 skipped（system Python）
  - `make lint` — passed
  - `make docs-check` — passed
  - `make test` — all checks passed（unit 729, integration 28, safety 35, property 2, Rust, Jest 109）
  - `/tmp/isaac_lab_study`: `python -m py_compile scripts/dam_safety_demo.py scripts/dam_teleoperate_demo.py tools/controll_scripts/safety/dam_wrapper.py tools/controll_scripts/safety/__init__.py` — passed
- **下一步**:
  - Commit this delivery unit
  - 下一交付單元：resolver-backed SafetyGuard EE path（只做 API-level resolver，不碰 runtime/sinks）

### Session 2026-06-08 #3 (resolver-backed SafetyGuard EE path)

- **本輪目標**: 讓 `SafetyGuard(input_space="ee")` 有真實可驗證的 resolver path，而不是只有 guardrail
- **已完成**:
  - 新增 `SafetyKinematicsResolver` Protocol（`inverse_kinematics`, `forward_kinematics`）
  - `SafetyGuard(..., kinematics_resolver=...)` 支援 EE pose action
  - EE path: target EE pose → resolver IK → `ActionProposal.target_joint_positions` + `target_ee_pose` → existing guard pipeline → resolver FK → safe EE pose
  - 無 resolver、dict EE action、resolver shape 錯誤都有明確錯誤
  - `dam.safe(..., input_space="ee", kinematics_resolver=...)` 也支援
  - 更新 `docs/library-api.md`
- **執行過的驗證**:
  - `.venv/bin/python -m pytest tests/unit/test_safety_guard.py -q` — 37 passed
  - `.venv/bin/python -m pytest tests/unit/test_config.py tests/unit/test_safety_guard.py -q` — 45 passed
  - `make lint` — passed
  - `make docs-check` — passed
  - `.venv/bin/python -m pytest tests/unit/ -x -q` — 732 passed
  - `make test` — all checks passed（unit 732, integration 28, safety 35, property 2, Rust, Jest 109）
- **下一步**:
  - Concrete Isaac resolver adapter（prefer sidecar first, then decide whether to upstream into `dam/adapter/isaac`）

### Session 2026-06-08 #1 (input_space 設計 + pip 打包回顧)

- **本輪目標**: 接手交接，設計 guard dual input space (joint/ee)，pip 打包 + Isaac Sim 整合收尾
- **已完成（設計定案，未實作）**:
  - `input_space` 欄位位置初步定案
  - 行為規則初稿
  - pip 打包與 Isaac 整合未記錄工作回顧
- **後續修正**:
  - 2026-06-08 #2 已將此設計收斂：第一步只交付 schema + SafetyGuard contract，不提前承諾完整 EE runtime 轉換或 Isaac EE snippet

### Session 2026-05-30 #2 (Boundary 拆分 + 語意修正 + Preset 解耦)

- **本輪目標**: 拆分 velocity/acceleration 為獨立 boundary，修正 fallback 語意，解耦前端 preset/boundary，新增 EE velocity limit
- **已完成**:
  - `joint_velocity_limit` 瘦身（移除 `max_acceleration`），新增 `joint_acceleration_limit` 獨立 callback
  - `BoundaryNode.fallback` 預設改為 `None`（clamp-only boundary 不帶 fallback）
  - `JointDef` 只保留 `name`，position limits 移到 boundary config
  - 新增 `ee_velocity_limit`（Jacobian-based EE speed cap，uniform scaling）
  - 5 個 docs 更新，stackfile 範本同步
  - 調查 `max_velocities < 2.0` 卡住問題（數學上不應卡住，可能被舊 accel+vel 交互導致，已修復需實測）
- **執行過的驗證**:
  - `python -m pytest tests/unit/ -x -q` — 668 passed, 31 skipped
  - `cd dam-console && npx jest --ci` — 109 passed
  - `make lint` — all passed
  - `make docs-check` — passed
- **改動檔案**: 16 files（未提交）

### Session 2026-05-30 #1 (JointLayout Contract)

- **本輪目標**: 建立 joint layout 合約，讓 callbacks 能區分 arm/gripper/base joints，支援多體機器人
- **已完成**:
  - `JointLayout` + `JointChain` data structures（chain-based model, gripper 是 chain 屬性）
  - 整合到 `RobotPreset`（不在 SafetyConfig）— preset 擁有物理描述
  - Name-based chains（用 joint 名字定義，不數 index）
  - Typo 偵測（difflib close_matches）、重複 assignment 偵測
  - Rich repr（顯示 joint names）、one-line summary
  - Zero-config for simple robots（auto-derive from joint_names keyword matching）
  - `pool["joint_layout"]` 在 runtime 啟動時解析，所有 callback 可讀
  - 30 unit tests（JointChain 3 + FromNames 5 + FromConfig 4 + Validation 4 + Queries 11 + PresetIntegration 3）
  - CBF analysis：SO-101 gripper 在 Jacobian 中接近零列，QP 自然 pass-through，不需強制切片
- **執行過的驗證**:
  - `python -m pytest tests/unit/ tests/safety/ -x -q` — 690 passed, 29 skipped
  - `ruff check` + `mypy` — all passed
  - `dam validate examples/stackfiles/*.yaml` — 6/6 valid
  - End-to-end: preset → joint_layout property → pool["joint_layout"] → queries
- **commits**: `e7b9625`, `df3d323`, `e985d4a`, `d509292`

### Session 2026-05-29 #6 (L1 CBF Unification — workspace, keep_out_zone, orientation_limit)

- **本輪目標**: 將所有 L1 kinematics callback 統一為 QP CBF contributor
- **已完成**:
  - `workspace` → CBF QPTerm(A, b) via `workspace_cbf_constraints`（4 new tests）
  - `keep_out_zone` (NEW) → sphere CBF QPTerm(A, b) via `sphere_keepout_constraints`（6 new tests + 1 QP integration test）
  - `orientation_limit` (NEW) → tilt CBF QPTerm(A, b) via `orientation_tilt_constraint`（5 new tests）
  - `J_angular` added to EE precomputation pool（1 line in execution_engine）
  - All three have halt fallback when Jacobian is unavailable
  - 8 doc files updated
- **執行過的驗證**:
  - `python -m pytest tests/unit/ -x -q` — 615 passed, 29 skipped
  - `python -m pytest tests/safety/ -x -q` — 35 passed
  - `ruff check` + `mypy` — all passed
  - `scripts/check_docs.py` — passed
- **commits**: `fc370c7`, `922d1b4`, `5625737`, `08e2ef1`, `40a07de`, `218d0cf`, `50f151f`, `c590b29`

### Session 2026-05-29 #6b (L2 Unification + Pool Forwarding + Review Fixes)

- **本輪目標**: L2 callback 統一讀 pool ee_pos、review findings 修復
- **已完成**:
  - ExecutionGuard 轉傳 runtime_pool 給 L2 callbacks
  - 新增 `task_workspace_bounds` 和 `task_joint_speed_limit` L2 callbacks（之前只在文檔，未實作）
  - 統一 pool forwarding：engine builds once → guards forward（不再自建 pool）
  - ValidationContext 帶 config_pool，_build_runtime_pool merge 進去
  - Fix: sphere center zero-normal（用 arbitrary escape direction）
  - Fix: CBF callbacks 從 pool 接收 actual dt（不再 hardcode 0.02）
  - Fix: MotionGuard 也轉傳 pool（之前自建 pool 丟掉了 ee_pos 等預計算）
  - 7 個新測試（2 pool forwarding + 3 task_workspace_bounds + 2 task_joint_speed_limit + 1 sphere-center）
- **執行過的驗證**:
  - `python -m pytest tests/unit/ tests/safety/ tests/property/ -x -q` — 660 passed, 29 skipped
  - `ruff check` + `mypy` — all passed

### Session 2026-05-29 #1 (GuardRuntime Decomposition + Legacy Shim Elimination)

- **本輪目標**: 拆分 1726 行 GuardRuntime God Object 為 5 個模組，消除 legacy callback shim
- **已完成**:
  - Extracted `_stackfile_builder.py` (296 lines) — stackfile → runtime construction
  - Extracted `_context_state_machine.py` (221 lines) — context stack push/pop/routing
  - Extracted `_cycle_telemetry.py` (284 lines) — loopback, frame hub, failure harvest
  - Extracted `_hot_reload.py` (141 lines) — thread-safe config swap double-buffer
  - GuardRuntime reduced from 1726 → ~1037 lines
  - Added ~75 lines backward-compat delegators (zero test modifications needed)
  - Deleted `dam/guard/callbacks.py` (legacy shim, 103 lines)
  - Rewrote `ExecutionGuard` to use `run_callbacks` + `aggregate` directly
  - Rewrote `HardwareGuard` to use `run_callbacks` directly (manual worst-result + flat metadata merge)
  - Assessed injection systems: static (guard.check) and pipeline (callback) are NOT redundant — different consumers, different lifecycles
- **執行過的驗證**:
  - `python -m pytest tests/unit/ -x -q` — 616 passed, 28 skipped (each commit verified independently)
  - pre-commit hooks (ruff + mypy + format) — all passed on each commit
- **commits**: `f68501c`, `36187e7`, `949e71f`, `e488008`, `81cd86a`

### Session 2026-05-29 #2 (Features: Percentile OOD + Action Smooth)

- **本輪目標**: 完成 P0/P1 backlog — workspace tests, OOD threshold, action smoothing
- **已完成**:
  - 7 workspace callback unit tests (inside/outside bounds, no-EE, default bounds, cbf_gamma validation, clamp behavior, motion_qp metadata)
  - **Percentile-based OOD threshold**: stores 6 percentiles (90-99.9) at training time; new `threshold_percentile` param in `ood_detector`; supports flow + memory_bank backends; interpolates between stored percentiles
  - **`action_smooth` L1 callback**: EMA-based oscillation damping (alpha=0.01-1.0, default 0.5); first cycle pass-through; smoothes with previous target each subsequent cycle
  - Confirmed MCAP Risk Log replay already implemented (backend + frontend)
  - Updated all progress docs
- **執行過的驗證**:
  - `python -m pytest tests/unit/ -x -q` — 636 passed, 29 skipped
  - pre-commit hooks — all passed on each commit
- **commits**: `ff77cad`, `107a7ed`, `38d243a`

### Session 2026-05-29 #5 (QP Mandatory + EE Precomputation)

- **本輪目標**: QP 強制化、刪除弱 callback、EE 預計算注入、L2 pool 讀取
- **已完成**:
  - 刪除 `keep_out_zone`、`orientation_limit`、`base_geofence`（REJECT-on-obs，無 QP）
  - MotionGuard 預設使用 `motion_qp_aggregator`，移除 sequential fallback
  - Stackfile builder 精簡：只驗證 proxsuite 可用性
  - `execution_engine._build_runtime_pool` 預計算 FK → `pool["ee_pos"]`, `pool["ee_rot"]`, `pool["J_linear"]`
  - `_accumulate_phase_clamps` 在 L1 clamp 後重算 FK，更新 pool
  - `workspace` callback 讀 pool `ee_pos`（fallback 到自算 FK）
  - `task_gripper_command_guard` 讀 pool `ee_pos`（fallback 到 `obs.end_effector_pose`）
  - 對齊 docs、examples、templates（刪除 qp_solver/cbf_gamma 殘留）
  - QP 數值容差調整（safety/property/integration tests 放寬到 1e-3）
- **執行過的驗證**:
  - `make test` — ALL PASSED
- **commits**: `b3dd856`, `73c8ed6`, `1300d1e`, `6246d33`

### Session 2026-05-29 #4 (L1 Kinematics Consolidation)

- **本輪目標**: 解決 L1 爆衝問題 — 加速度限制 + 清除無效 callback
- **已完成**:
  - `joint_velocity_limit` 加入 `max_acceleration` 參數 — 兩段式 clamp（先限加速度再限速度），追蹤前一 cycle 速度
  - 刪除 4 個無效 callback：`check_velocity_smooth`（REJECT on obs）、`action_smooth`（EMA 無硬保證）、`cartesian_velocity_limit`（REJECT on obs, 無 QP）、`check_joints_not_moving`（場景太窄）
  - `workspace` 簡化：移除 CBF/QP metadata（與 L2 task stages 衝突），純位置框 + 急停
  - 更新文檔（boundary-callbacks.md, concepts/boundaries.md, adversarial-testing.md）
  - 6 個新加速度測試（sudden jump, smooth pass, deceleration, metadata）
- **執行過的驗證**:
  - `make test` — ALL PASSED (666 unit + 28 integration + 43 safety + 2 property + 55 Rust + 109 frontend)
- **commits**: `84745fa`, `7f39640`
- **淨效果**: -573 lines, +201 lines = 淨刪 372 行

### Session 2026-05-29 #3 (Test Fix + Full Review)

- **本輪目標**: 修復所有 `make test` failures，分段 review items 2 & 3
- **已完成**:
  - Fixed `test_dataset_and_live_camera_images_reach_policy_without_republishing_live_frame` — monkeypatch target updated after GuardRuntime telemetry extraction (`runtime._telemetry.publish_frames_to_hub`)
  - Completed 3-segment code review: legacy shim elimination, percentile OOD threshold, action_smooth
  - Confirmed injection systems assessment: static vs pipeline injection are NOT redundant
- **執行過的驗證**:
  - `make test` — ALL PASSED (686 unit + 28 integration + 47 safety + 2 property + 55 Rust + 109 frontend)
- **commit**: `7bba34f`

### Session 2026-05-28 #2 (L3 Hardware DX Audit)

- **本輪目標**: 以開發者角度審計 L3 Hardware 層，修復會讓開發者傻眼的設計
- **已完成**:
  - Consolidated `force_limit` + `check_force_torque_safe` → `force_torque_limit` (channel fallback + force/torque dual check)
  - Deprecated old force callbacks with one-time `DeprecationWarning`
  - Fixed `host_health_limit` guard_name mismatch (`"host_health"` → `"host_health_limit"`)
  - Added NaN/Inf protection to 4 telemetry callbacks — fixes voltage_limit empty-reason REJECT on NaN
  - Removed `json.dumps/loads` round-trip in `collect_host_health`
  - Added `_reset_host_health_cache()` for test isolation
  - Investigated missing-channel → PASS behavior (intentional, tested; config-time validation gap spawned as task)
  - 11 new tests (7 force_torque_limit + 4 NaN/Inf protection)
- **執行過的驗證**:
  - `python -m pytest tests/unit/ -x -q` — 616 passed, 28 skipped
  - `ruff check` + `mypy` — all passed
  - pre-commit hooks — all passed
- **commit**: `c8c51b2`

### Session 2026-05-27 #1 (IL Safety Integration API)

- **本輪目標**: 設計開發者友好的 IL 整合 API — SafetyGuard / safe() / SafetyProcessorStep / make record
- **已完成**:
  - `dam.SafetyGuard(stackfile)` — 有狀態的 guard，支援 dict/ndarray 雙格式、度/弧度自動轉換
  - `dam.safe(action, obs, stackfile)` — 一行搞定的便利函數
  - `dam.SafetyProcessorStep` — lerobot RobotActionProcessorStep 子類，加一行就能安全錄製
  - `dam.processor.make_safe_processors()` — make_default_processors 的安全版替代
  - `scripts/record.py` — safe recording wrapper，注入 SafetyProcessorStep
  - `make record` Makefile target
  - `examples/safe_record.py` — 三層 API 範例
  - 20 個新增測試（SafetyGuard 10 / safe 3 / ProcessorStep 7）
- **執行過的驗證**:
  - `.venv/bin/python -m pytest tests/unit/test_safety_guard.py -x -v` — 20 passed
  - `.venv/bin/python -m pytest tests/unit/ -x -q` — 641 passed (含 regression)
  - `.venv/bin/ruff check dam/ tests/ examples/ scripts/record.py` — all passed
  - `.venv/bin/python examples/safe_record.py` — 三層 API 全部正確運行
  - `make check` (pre-commit hooks) — all passed
- **API 設計亮點**:
  - 輸出形狀與輸入完全一致（dict→dict, ndarray→ndarray）
  - Reject 時自動 hold position（不中斷 recording loop）
  - degrees_mode 從 preset 自動推斷
  - ProcessorStep lazy init（避免 import-time side effects）
  - `scripts/record.py` 從 stackfile 的 `hardware:` 讀取 robot/cameras/teleop，從 `recording:` 讀取 dataset/display/resume，全部轉為 lerobot-record CLI args
  - 用戶只需編輯一個 YAML 檔就能設定硬體 + 安全邊界 + 錄製參數
- **commits**: `adc735a`, `2ecd7cb`, `8ae30e5`, `ba27b8c`, `f18cb78`, `2c87e47`

### Session 2026-05-27 #2 (make record 實戰修復)

- **本輪目標**: 修復 `make record` 端到端運行問題
- **已完成**:
  - `scripts/record.py` 讀取 `hardware:` 段並轉為 lerobot-record CLI args（robot/cameras/teleop）
  - 精簡 log（壓掉 lerobot config dump，加 `--verbose` flag）
  - Rerun SDK 安裝加入 `scripts/setup.sh`
  - `.venv/bin` 加入 PATH 修復 rerun viewer binary not found
  - `resume: true` 防呆：dataset 不存在自動降級 + 清理 stale cache 空目錄
  - README 重寫：~390→~140 行，用實際架構圖，修正 L0/L1 平行
- **執行過的驗證**:
  - `python -m pytest tests/unit/test_safety_guard.py -x -q` — 20 passed
  - `record.py` dry-run — 正確生成 14 個 args
  - resume 防呆 — 自動清理 stale dir + 降級成 resume=false
  - pre-commit hooks — all passed
- **commits**: `adc735a`, `2ecd7cb`, `8ae30e5`, `ba27b8c`, `f18cb78`, `2c87e47`

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

### Session 2026-06-12 #1 (teleop inertia root-cause fix)

- **本輪目標**: 修復 `make record` 動作慣性（follower 甩過頭再慢慢回來）
- **根因**: `_remember_validated_action`（commit 05ec93d）把前一輪速度推導為 (命令 − 實測)/dt，把 follower 物理追蹤落差混進「命令速度」；加速度限制器保留這股幽靈動量，operator 停止時把命令甩過真實位置，再被 accel/vel limit 慢慢拉回
- **已完成**:
  - `_remember_validated_action` 改為純命令域：velocity = (target_t − target_{t−1})/dt，首輪無歷史則為 None
  - `joint_acceleration_limit` 全面命令域化：proposed velocity、prev velocity、clamp 位置重建、QP box 全部錨定前一輪命令（`anchor`），實測位置只在首輪無歷史時當 seed
  - `_prev_vel` cache 擴為 (velocity, command_positions, timestamp) 三元組，standalone 路徑同樣命令域
  - 還原 safety.yaml 治標性放寬（max_velocities 7→4, max_acceleration 15→10）
  - Regression tests ×2（tracking lag 不得產生幽靈動量；clamp 錨定命令而非實測）+ runtime injection test 更新為命令域語義
- **執行過的驗證**:
  - `pytest tests/unit/` — 800/800 passed
  - `pytest tests/safety/ tests/property/` — 37/37 passed
  - mypy + ruff 通過
  - 閉環模擬 A/B（leader 3 rad/s 急停 + 100ms 一階滯後 follower）：舊語義命令過衝 140.5 mrad (8.1°) → 新語義 40.8 mrad (2.3°，純減速度物理必然量)，皆收斂
- **已知風險或未解決問題**:
  - 未 commit（guard_runtime.py 同檔還有 in-flight slow-lane 改動，需一起或分開 commit 由使用者決定）
  - 無實機驗證；建議下次 `make record` 實測甩動是否消失
- **下一步最佳動作**: 實機驗證後 commit；若 4 rad/s velocity limit 造成跟隨太慢，再針對性調參（這次是有根因依據的調整，非治標）
