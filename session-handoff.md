# session-handoff.md — 會話交接摘要

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


## 2026-10-16 — 錄製資料與 Risk Log 靜態使用情境檢查

使用者再次要求不要編譯或運行代碼。本輪仍未執行程式、測試、linter、服務、影像播放或實際刪除資料；只修改檔案並做人工靜態審查與 `git diff --check`。

修改 `McapCameraPlayer.tsx`、`McapSessionList.tsx`、`app/mcap-viewer/page.tsx`、`RiskLogTable.tsx`：修正跨錄製檔的過期影像／週期資料、首幀 live image、初始影像時間對齊、ID 0、刪除提示與部分成功處理，以及 Risk Log 舊請求覆蓋新篩選結果。後端永久刪除行為不變，本輪沒有呼叫任何服務 API。使用流程文件補充刪除行為。

新增 `src/__tests__/components/` 下的 `McapCameraPlayer.test.tsx`、`McapSessionList.test.tsx`、`McapViewer.test.tsx`、`RiskLogTable.test.tsx` 共 11 個回歸案例。允許執行後，先驗證這四個檔案，再視結果擴大前端測試。所有執行結果目前未知，亦未執行型別檢查。保留其他現有／進行中的變更，沒有提交。

---

## 2026-10-16 — 啟動與驗證流程續修（僅靜態審查）

使用者在本聊天再次確認「沿用限制，只做靜態修改與審查」。本輪沒有執行專案程式、測試、編譯、linter 或服務；只閱讀／修改檔案、人工比對與 `git diff --check`。先前的 918 passed 不涵蓋本輪變更。

- `scripts/service_helpers.sh` 統一 dev/run 的 readiness、服務存活監測、錯誤日誌與關閉流程。ready banner 放在 HTTP 檢查通過之後；curl 有連線與回應 timeout，`DAM_STARTUP_TIMEOUT` 可設定 1–3600 秒。任何服務意外退出都回報並結束本次啟動。
- 每次啟動保留獨立的 `logs/dev.*`／`logs/run.*` 目錄與 backend/frontend 日誌；錯誤顯示尾端內容。以 Bash job control 建立本次服務的 process groups，關閉時先 TERM、等 10 秒再清理剩餘子程序；此流程尚待 macOS Bash 3.2 與 Linux 實測。
- dev 明確使用 Next.js `--hostname 127.0.0.1 --port 3000`，避免依賴未必生效的 HOST 環境變數；readiness 與埠檢查目標一致。
- `scripts/test.sh` 缺少所選套件的依賴時回傳失敗，成功訊息限定於 requested checks；只跑 Rust／frontend 不再要求 Python venv。run/setup/test 的 NVM 探測允許目錄不存在，不因 `pipefail` 提前離開。
- 新增 `tests/unit/test_service_helpers.py`，補充 `tests/unit/test_local_helpers.py`：ready／timeout、意外退出、子程序清理、無子程序關閉、無效 timeout、缺少 frontend 依賴與 Rust-only 無 venv。這些案例皆未執行。

下一步需使用者允許執行後，先驗證兩個 helper 測試檔、shell syntax 與文件檢查，再評估隔離服務的生命週期驗證；不要直接啟動本地 `.dam_stackfile.yaml` 對應的真實 host。其他聊天進行中的 frontend／safety／runtime 修改未介入。

---

## 2026-10-16 — Stackfile 使用流程靜態檢查（禁止執行）

使用者明確要求先不要編譯或運行代碼；此限制仍有效。本輪未執行測試、linter、服務或實機操作，只做檔案閱讀／修改、人工差異審查與 `git diff --check`。

修正範圍：`useStackfileLibrary` 的載入完成閘門、讀取失敗顯示與重試、過期目標 save callback 防護、刪除前讀取 Live；Config/Guard 載入時不自動重寫 YAML、切換空設定清除舊規則；parser 的空 mapping；新增 `POST /api/stackfiles/{name}` 以 exclusive file creation 防止 New 同名覆寫，保留 PUT 儲存行為並檢查 YAML body 型別。

待允許執行後驗證：`tests/unit/test_stackfile_library.py`，前端 `StackfileEditors.test.tsx`、`useStackfileLibrary.test.ts`、`api.test.ts`、`templates.test.ts`，再依結果決定是否擴大檢查。這些回歸案例已寫入，尚未執行，不能視為通過。使用流程文件已同步。其他進行中的 safety/runtime/sidebar 等檔案不屬於本輪修改，未回復或提交。

---

## 2026-10-16 — 操作摩擦點優化

已將 Make 的 Python CLI／驗證工具改為 module invocation；可直接用 `make dam ARGS="inspect"`、`make validate`、`make docs-check`，不依賴舊 console-script 路徑。`init.sh` 固定使用專案 Python 並可從其他目錄呼叫。dev/run 不再 kill 佔用 ports 的程序，會先回報；production 缺 frontend build 時不啟動 backend。

`dam inspect` 新增 enforcement mode、全域規則與各 task 合併後的設定清單。agent 舊說明改為 skill 入口，修正文檔落差；mobile-base 範例改以 context manager 清理資源並聚合顯示最嚴格 decision。執行範例使用 `.venv/bin/python -m examples.mobile_base_guardrail`。

驗證：33 targeted tests、918 unit tests、9/9 example Stackfiles、docs-check、ruff、shell syntax 與 skill validator 通過；離線範例 PASS/REJECT 正常。初始 replay/QP baseline 失敗已在其他進行中變更更新後消失，本輪沒有修改那些 guard/QP 或 replay-test 檔案。未啟動實機或 backend/frontend；port 檢查使用獨立測試 listener 驗證不會終止既有服務。

---

## 2026-10-16 — DAM agent skill

已新增 `skills/dam-agent/SKILL.md`、`agents/openai.yaml` 與 `references/project-map.md`，並於 README 提供入口。技能以現行 schema/API/tests 為查證基準，涵蓋設定、整合、核心維護與實機事故的唯讀取證；避免照搬舊 LLM prompt。已透過 symlink 安裝到 `~/.codex/skills/dam-agent`，可用 `$dam-agent` 呼叫。

驗證：skill validator、文件檢查、離線 PASS/CLAMP 範例、來源路徑檢查與 minimal Stackfile validate/inspect 通過；本輪 unit baseline 為 886 passed。CLI wrapper 有舊 interpreter 路徑，已使用 `.venv/bin/python -m dam.cli` 繞過並於技能記錄處理方式。無實機驗證。工作期間出現其他進行中的 guard/QP 修改，本交付未修改或回復那些檔案，baseline 結果不代表對那些後續改動的驗收。

---

> 最後更新: 2026-06-14 (Guardrail dict-in API + 0.7.0 收尾)

## 本輪：Guardrail（filter API 重設計，dict 進／validated command 出）

承上輪 preset/solver 收尾，使用者指出 filter API 的 `SafetyGuard(action, obs)` 用兩個**扁平 joint 向量**是錯的——obs 本質是多組異構資料（joints / 相機 / 電流 / base pose），不該被當成一個 joint 向量；要對齊 `step()` 的 Observation 模型，讓 callback 按 key 取分組。決策（已與使用者確認）：扁平 lerobot 風格 dict + `action` 保留 key、callback 直接宣告 key 注入、init 印契約且缺 key fail-fast、命名改 `Guardrail`/`guardrail()`。

- **`SafetyGuard`→`Guardrail`、`safe()`→`guardrail()`、`SafetyProcessorStep`→`GuardrailProcessorStep`**（無 shim，0.7.0 breaking）。`__call__(action, obs)` → `__call__(inputs: dict)`：`action` 保留 key 是待驗證命令，其餘 key 是 obs 分組。回傳鏡像 `action` 輸入型別。
- **obs 分組注入**：`Observation.channels`（current/temperature/base_pose…）+ images 在 `execution_engine._build_runtime_pool` 平鋪進 pool（`_iter_obs_groups`，只收合法識別字、不蓋保留 key）。callback 宣告 `base_pose`/`current` 直接拿。`dam/api.py:Guardrail._build_observation` 把 dict 拆成標準欄位（joints/`.pos`→joint_positions、images、其餘 array→channels）。
- **自動契約 + fail-fast**：init 掃 active task 的 callback 簽名（`get_params`/`inspect`）減保留 key 與 node params＝required obs keys；`describe()` 印一次；每次 call 缺 required key 或撞保留 key → raise。
- **`Observation.joint_positions` 改可空**（mobile base 無 joints，狀態走 channels）；`_compute_fk` 加 `len>0` 守衛。
- **`ActionProposal` duck-type 成命令向量**（`__array__`/`__iter__`/`__len__`/`__getitem__`），讓 callback 寫 `v, omega = action` / `np.asarray(action)`，builtin 仍用 `.target_joint_positions`。
- **`@dam.callback(name, *, layer="L1")`** 現在設 `fn._cb_layer`（之前沒設，導致 custom callback 被 L 層 guard 的 `expected_layer` 過濾掉、靜默不執行）。
- **command space vs joint space**：action_layout 的 keys≠joint_names → command space（不做 deg↔rad scale、reject 回 `safe_action`）。`safe_action`：vector / `"hold"`（arm 預設，回當前 joint）/ `"zero"`。EE-pose（typed segment ee_pose=7/scalar=1）由 `_segment_size`/`_refresh_action_spec` 處理；非 joint-size 的 command-space stackfile 不應放 joint guard（EE 測試改用無 joint-clamp 的 `ee_stackfile`）。
- **新 example** `examples/mobile_base_guardrail.py`（~55 行，純 numpy，註冊 solver+callback → `Guardrail` → 每 cycle 丟 dict）取代使用者原本 180 行的 `JetbotDAMWrapper`。jetbot stackfile callback 改名 `rollout_inside_band`。
- **版本/文檔**：pyproject+package.json 已在前單元 bump 0.7.0；release-notes-0.7.0 加 Guardrail 章節＋migration 第 6 條；library-api / safe-recording / use-cases 全改新 API。

驗證：721 unit + 292 safety/property/相關 integration 全過；ruff/docs-check clean；mobile_base_guardrail 與 safe_record 兩個 example 實跑 PASS/CLAMP/REJECT 正確。

剩餘風險：`examples/isaac_jetbot_lane_demo.py` 仍 import 外部 `controll_scripts.safety`（使用者本地），未隨改；其 docstring 指向新 example。`safe_record.py` Level 2 首 cycle 顯示較大 clamp（無 prev velocity baseline 的既有行為，非回歸）。

---

> 上一輪更新: 2026-06-14 (Preset/interface semantics follow-up — 兩個交付單元)

## 本輪追加（第二單元：hz 改名 + capabilities + degrees_mode 搬位置）

接續第一單元，依使用者後續指示再做：

- **三個 hz 統一改名並全收進 `safety`**（不留 back-compat alias，舊名直接拒收）：
  - `safety.control_frequency_hz` → `safety.control_hz`（內部 Python 屬性也改名；runner 自己的 `control_frequency_hz` kwarg / `_control_frequency_hz` / runner-meta 不動）
  - `safety.slow_lane.frequency_hz` → `safety.slow_lane.task_hz`（async task/OOD lane 評估率；SlowLaneWorker 的 `frequency_hz` 建構參數保留不動）
  - `telemetry_hz` 從 `hardware` 搬到 `safety`（控制項）；`hardware.telemetry_hz` 欄位移除，factory 新增 `_resolve_telemetry_hz` 只讀 safety
- **solver capabilities `[kinematics, fk, ik]` → `[fk, ik]`**：`kinematics` 與 solver 名字（arm_kinematics）語意重複。`_get_ee_pose` 的 solver 選擇從查 `"kinematics"` 改查 `"fk"`；builtin pinocchio factory capabilities 改 `("fk","ik")`。
- **franka preset 移除 `chains`**：`JointLayout.from_names` 的 gripper 關鍵字含 `finger`，franka 的 `panda_finger_joint1/2` 自動歸 gripper、其餘 7 軸歸 arm，與舊 explicit chains 結果一致（已驗證 arm[0-6]+gripper[7,8]）。
- **degrees_mode 不再是 robot identity**：從 `RobotPreset` / `presets.yaml` / preset router / 前端 PresetManager 全移除。改由 motor interface 宣告（`source.degrees_mode`），interface-less 配置用 `hardware.degrees_mode`。新增 `HardwareConfig.motor_degrees_mode(default=True)`：motor source → hardware.degrees_mode → 預設 True（lerobot 馬達度數原生）。factory/runner/api.SafetyGuard 全改用它，不再讀 `preset.degrees_mode`。
  - SO-101 examples 的 motor interface 補 `degrees_mode: true`；franka_safety 補 `hardware.degrees_mode: false`（弧度原生，無 motor interface）。行為與舊 preset 完全一致（已 smoke 驗證 7 個 example）。

驗證：801 unit + 65 integration/safety/property + 114 jest 全過；ruff/format/tsc clean。

---

## 第一單元：keys-based action_layout + telemetry type inference

## 本輪重點：preset/interface 語意收尾（接續 commit 5241ffc）

上一個 commit「Redesign preset and interface semantics」漏了 YAML 層的幾項對齊，本輪補齊：

- **action_layout 改 keys 形**：每個 segment 用 `keys: [...]` 列出每個 slot 的語意，size 自描述（== len(keys)）。joint 段 keys=關節名；ee 段 keys=`[x,y,z,yaw,pitch,roll]` 或 quaternion；差速=`[v,omega]`。根除舊 `type` 靠 `{"ee_pose":7,"scalar":1}` 硬表查 size 的問題（`joint_position` 不在表裡，so101 段以前根本切不出來）。`dam/api.py:_split_raw_action` 改用 `len(keys)`，保留 size/type 為 legacy fallback。
- **telemetry channel 免寫 type**：`temperature: {capabilities:[robot_telemetry], ref: arm}` 即可，key 名即 type。`HardwareConfig` 新增 `model_validator(mode="before")` `_default_interface_type`，把缺 type 的 interface 補成 key 名，所以 `type` 在下游仍是必填（factory 不用處理 None）。
- **presets.yaml 對齊**：so101（pinocchio solver 保留，arm 5 關節 + gripper keys 形）、franka（關節空間 keys 形 arm 7 + gripper 2）。
- **examples 清理**：拔掉每個檔殘留的斷尾註解 `# by the preset's action_layout.`、telemetry 段移除 `type:`。
- **前端**：templates.ts channel renderer 不再輸出 `type:`；parser 改用 `capabilities:[robot_telemetry]` 偵測 channel；PresetManager placeholder 改 keys 形。
- **docs**：library-api.md / quick-stack.md 的 action_layout 範例改 keys 形。

**franka USD + isaac_kinematics 決策**：`isaac_kinematics` solver 尚未註冊、franka 無 bundled asset，硬塞會變成 solver 靜默 skip 的假可跑配置。因此出貨 preset 維持關節空間可跑；usd/isaac/ee_pose 示意留在 `test_hardware_accepts_action_layout_and_solver_overrides`（schema 接受測試）+ docs override 範例。若 isaac solver 即將實作，再把 franka preset 切到 ee_pose。

**telemetry_hz vs slow_lane**：兩者非重複——`telemetry_hz` 是馬達匯流排暫存器讀取的 decimation（硬體 IO cadence）；`slow_lane.frequency_hz` 是昂貴 guard 在 async worker 的評估頻率。兩者保留，so101.yaml 補了釐清註解。

驗證：801 unit + 114 jest 全過；ruff/format/tsc clean；7 個 example stackfile 全部 smoke-load 成功，telemetry type 正確推導；兩 preset 的 action_layout keys 加總 == 關節數。

---

> 前一輪: 2026-06-12 (Teleop inertia root-cause fix — acceleration limiter in command domain)

## 本輪重點：teleop 慣性根因修復

`make record` 出現「甩過頭再慢慢回來」的慣性，根因是 commit 05ec93d 把前一輪命令速度推導為 (命令 − 實測)/dt，把 follower 的物理追蹤落差混進速度，加速度限制器將其保留為幽靈動量。修復後：

- `GuardRuntime._remember_validated_action`：fallback 速度改為命令對命令 (target_t − target_{t−1})/dt，首輪為 None，觀測值完全退出此路徑
- `joint_acceleration_limit`：proposed/prev velocity、clamp 重建、QP box 全部錨定前一輪命令（無歷史時才用實測 seed）
- `_prev_vel` cache 變為 `(command_velocity, command_positions, timestamp)` 三元組
- safety.yaml 治標性放寬（vel 7 / accel 15）已還原為 4 / 10
- 驗證：800 unit + 37 safety/property 全過；閉環模擬 A/B 過衝 140.5 → 40.8 mrad
- **未 commit**（guard_runtime.py 與 in-flight slow-lane 改動同檔）；待實機驗證

## 專案現況一句話

DAM 的 L1 kinematics safety pipeline 已完成全面 QP 約束覆蓋：所有 7 個 L1 callback 都向 QP aggregator 提供 metadata，multi-constraint 場景可正確 fuse 為單一最小侵入安全動作。

## 使用者可見行為變化（相對於上次 release）

1. **Multi-constraint 行為更平滑** — 之前加速度/EE速度約束跟其他約束各做各的 clamp，可能互相衝突；現在 QP solver 一次性滿足所有約束。
2. **Pipeline restart 不再 false alarm** — 之前 pipeline stop → start 可能因 stale velocity history 在第一個 cycle 觸發 acceleration clamp；現在有 timestamp staleness detection 自動 reset。
3. **雙臂/多 boundary 場景正確隔離** — 之前多個 boundary container 共用 `joint_acceleration_limit` 會互相污染 velocity history；現在 state 按 boundary_name scope。
4. **EE resolver path hardened** — IK/FK 異常、NaN、dimension mismatch 都會降級（hold position / last known pose），不再 crash control loop。
5. **Deprecated API 移除** — `check_force_torque_safe` / `force_limit` 已刪除，無 deprecation warning。

## 技術架構要點

### L1 QP 約束完整清單

| Callback | QPTerm 形式 | 約束語義 |
|----------|-------------|----------|
| `joint_position_limits` | box bounds | 關節位置 [lower, upper] |
| `joint_velocity_limit` | box bounds | 速度限制 → position range |
| `joint_acceleration_limit` | box bounds | 加速度限制 → velocity → position range |
| `ee_velocity_limit` | A-matrix (linearized) | `d^T @ J @ v <= max_v` |
| `workspace` | A-matrix (CBF) | EE position box constraint |
| `keep_out_zone` | A-matrix (CBF) | 球形禁區 CBF |
| `orientation_limit` | A-matrix (CBF) | EE tilt limit CBF |

### _prev_vel 機制

- `dict[str, tuple[np.ndarray, np.ndarray, float]]` — key 為 `boundary_name`，value 為 `(command_velocity, command_positions, monotonic_timestamp)`；純命令域歷史，standalone 路徑專用（runtime 路徑由 prev_validated_* 提供）
- Staleness factor: `5.0 * dt` — 超過此時間自動 reset baseline
- `boundary_name` 由 `dam/guard/pipeline.py:run_callbacks()` 注入到 callback kwargs

### EE resolver path

```
EE pose → resolver.inverse_kinematics() → joint proposal
       → existing guard pipeline (all L1 callbacks)
       → resolver.forward_kinematics() → safe EE pose
```

Failure modes: IK exception → hold position; FK exception → last known pose; NaN → hold position.

## 驗證證據

- `make test` — all checks passed
- pytest: 756 unit + 28 integration + 35 safety + 2 property
- Rust: all passed
- Jest: 109 passed
- 0 ruff errors, 0 mypy errors
- pre-commit: all hooks passed

## 已知限制

- `ee_velocity_limit` 的 QPTerm 是一階線性化（Taylor at current direction），大角度偏轉時保守度略高
- `_prev_vel` 仍是 module-level global（不是 instance attribute），但已用 boundary_name + staleness 完全隔離，無實質風險
- 無實機驗證；所有行為由 unit/integration/safety tests 覆蓋

## 下一步建議

1. **EE-policy end-to-end snippet** — 用 resolver path 寫一個極小 demo，驗證從 EE target → IK → guard pipeline → FK → safe EE 的完整 round-trip。可在無 Isaac 環境下做。
2. **IsaacLab runtime 驗證** — 用 `scripts/dam_safety_demo.py --controller ik` 確認 Pinocchio FK frame 與 Isaac body frame 對齊。需 Isaac runtime。
3. **QP solver 性能** — 現在 7 個 QPTerm 都 active 時，ProxSuite QP 解算時間是否仍在 cycle budget 內？需在真實 50Hz 場景 profiling。
4. **不要提前加 `target_ee_pos` 到 runtime pool** — 等真正有 EE policy runtime 需求時再決定。
