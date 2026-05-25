# Guard Pipeline 統一重構 — 交接筆記

> Working doc — refactor in progress. Update each commit.
> Last updated: 2026-05-21

## 動機

四個 builtin guard（OOD/Motion/Execution/Hardware）雖然都繼承 `Guard`，但實際分工與資料來源差異很大：

- **MotionGuard** 把所有 L1 約束（`upper / lower / max_velocity / bounds / qp_solver / cbf_alpha …`）寫死在 `check()` 簽名，hot path 內部 normalize 單位、cache 參數。
- **ExecutionGuard / HardwareGuard** 已經透過 `evaluate_boundary_callbacks` 把實作 dispatch 給 boundary callbacks，但 callback 只回 `bool`，無法 CLAMP。
- **OODGuard** 自帶 ML state（feature extractor / memory bank / flow），不走 callback 機制。

使用者新增一條 L1 約束要動 3 處：MotionGuard 加 kwarg、寫檢查邏輯、修 cache key。目標是 **新增 callback 只要寫一個 function**，guard 一行不動。

## 設計決策（記錄 + 否決原因）

### 1. `expected_decisions` 直接放在 abstract `Guard`，concrete guard 各自宣告

否決過的方案：抽出 `RejectingGuard` / `ClampingGuard` / `FaultingGuard` 三個中間 subclass。

否決原因：那不是 OO — 中間 class 沒有自己的行為，只是把 ClassVar 換個位置宣告。每個 guard 直接 override `expected_decisions` 已經夠了。`HardwareGuard` 同時要 PASS/CLAMP/FAULT 也不被分類框架綁住。

### 2. Callback 不該預設「fragment kind」語意

否決過的方案：定義 `ConstraintFragment(kind=Literal["box","linear"], upper, lower, A, b, ...)`。

否決原因：框架不該決定使用者怎麼表達約束。「box / linear / CBF」這些是 *某個 solver 的實作細節*，不是通用語意。使用者寫 callback 想 clamp，就自己算出 `clamped_action` 回來。要進階融合（QP），自己提供 aggregator + 在 `metadata` 放 solver 所需資料。

### 3. L2/L3 也要能 CLAMP

否決過的假設：「L1 才能 CLAMP，其他層只能 REJECT/FAULT」。

否決原因：L3 thermal throttle、L2 task-level slowdown 都是合理的 CLAMP 用例。callback 結果型別跨層統一。

### 4. 資料來源：靠 callback signature + InjectionResolver

`@boundary_callback` 已經透過 function signature 從 pool 自動撈 kwargs（[`dam/injection/resolver.py`](../dam/injection/resolver.py)）。MotionGuard 也要走這條，不該再有自己的 `check()` kwargs list。

`dt` / `dynamics` 等 runtime 注入物進 `RUNTIME_POOL_KEYS`，callback 想用就在簽名宣告。

## 統一架構

### 共用型別 — `dam/guard/pipeline.py`

```python
@dataclass(frozen=True)
class CallbackResult:
    decision: GuardDecision         # PASS / REJECT / CLAMP / FAULT
    boundary_name: str
    reason: str = ""
    clamped_action: ValidatedAction | None = None    # CLAMP 時填
    fault_source: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, boundary_name) -> CallbackResult: ...
    @classmethod
    def violate(cls, boundary_name, reason) -> CallbackResult: ...
    @classmethod
    def clamp(cls, boundary_name, clamped_action, reason="") -> CallbackResult: ...
    @classmethod
    def fault(cls, boundary_name, exc, source) -> CallbackResult: ...
```

### 共用 pipeline（所有 guard）

```
run_callbacks(active_containers, pool, layer) -> list[CallbackResult]
    └── 每個 callback 透過 InjectionResolver 自動從 pool 撈 kwargs

aggregate(results, *, action_in, guard_name, layer) -> GuardResult
    └── 任何 FAULT          → GuardResult.fault
    └── 任何 REJECT         → GuardResult.reject (reasons 串)
    └── 多個 CLAMP          → 預設 sequential 套用 clamped_action → GuardResult.clamp
    └── 全 PASS             → GuardResult.success
```

四個 guard 都盡量往同一個 callback/pipeline contract 靠攏。差別只在：
- 各 guard 可注入自己的 aggregator（MotionGuard 已有 opt-in QP aggregator）
- OODGuard 帶 ML state，先保留 model-driven guard；後續拆 backend，但不強迫 callback 化

## 階段進度

### 階段 0 ✅ — `expected_decisions` ClassVar

- [x] [`dam/guard/base.py`](../dam/guard/base.py) 加 ClassVar 註記
- [x] 4 個 guard 各自宣告：
  - `OODGuard`     = {PASS, REJECT, FAULT}
  - `MotionGuard`  = {PASS, CLAMP, REJECT, FAULT}
  - `ExecutionGuard` = {PASS, REJECT, FAULT}
  - `HardwareGuard`  = {PASS, CLAMP, FAULT}
- [x] [`tests/unit/test_guard_decision_contract.py`](../tests/unit/test_guard_decision_contract.py)

### 階段 1 ✅ — `CallbackResult` + 統一 pipeline

1. [x] 新增 [`dam/guard/pipeline.py`](../dam/guard/pipeline.py)：`CallbackResult` + `run_callbacks` + `aggregate` + `sequential_clamp_aggregator` + `run_and_aggregate`。預設 clamp fusion 用 `ValidatedAction.merge_restrictive`（element-wise 取最保守值）。18 unit tests 全綠。
2. [x] [`dam/guard/callbacks.py`](../dam/guard/callbacks.py) `evaluate_boundary_callbacks` 改成薄殼，內部呼叫新 pipeline；保留 `(saw_callback, GuardResult|None)` 對外介面避免 break 既有 L2/L3 呼叫端。
3. [x] L1 boundary callbacks（[`kinematics.py`](../dam/boundary/callbacks/kinematics.py)）`joint_position_limits` / `joint_velocity_limit` / `workspace` 從 obs-only `bool` 重寫為 action-CLAMP `CallbackResult`。沒有同時保留 REJECT 版本（dual-track 多餘）。`workspace` 沒 IK 時的 CLAMP 語義 = 凍結 action 到當前 obs（halt），明確標示在 docstring；要 QP/CBF 的人自己注入專用 aggregator + 在 callback `metadata` 放資料。
4. [x] `MotionGuard` 從 430 行縮到約 70 行 — 只是 pipeline 的 thin shell。建構子接受可選 `clamp_aggregator`（user 想注入 QP 在這條路）。
5. [x] `_register_guard_if_new` (guard_runtime.py): 把 L0/L1/L2/L3 都改成「保留 boundary callback 名稱在 constraint 上」（之前只有 L2/L3 保留），L0 OODGuard 例外因為它不走 callback 派遣。
6. [x] `execution_engine._run_stage_sequential` 非 boundary-specific guard 改傳 `boundary_name=None` 給 `_run_one_guard`，避免把 `active_containers` 縮到單一 boundary —— MotionGuard 需要看見所有 active L1 containers 才能跨 boundary 聚合。
7. [x] 三個 bench/scan/quality script ([run_latency_bench.py](../scripts/run_latency_bench.py)、[run_boundary_scan.py](../scripts/run_boundary_scan.py)、[run_record_quality.py](../scripts/run_record_quality.py)、[run_usability_study.py](../scripts/run_usability_study.py)) 改成走 stackfile YAML → `GuardRuntime.from_stackfile()` → `runtime.validate()`，跟 production 同一條路徑。新建 [`scripts/_bench_stackfiles.py`](../scripts/_bench_stackfiles.py) 集中產生 in-memory stackfile 給 4 個 script 共用。
8. [x] 432 unit tests passed，2 個 pre-existing 環境失敗 deselected，2 個 QP 直接 inline test 暫時 skip 標記等階段 3 處理。

**未解決，留給後續階段**：
- ~~階段 3：MotionGuard `clamp_aggregator` 抽出「QP fusion strategy」~~ ✅ 完成，見下方階段 3。
- 階段 4：OODGuard 還是 model-driven、不走 callback pipeline。如果要對齊可以拆 backend 後讓 callback 跑 backend，但要小心 model state 的 lifecycle。

### 階段 2 ✅ — 統一 dt 來源（隨階段 1 自然落地）

MotionGuard 自己從 `obs.timestamp` 推 `effective_dt` 那段已隨著 MotionGuard 重寫一併刪除。`dt` 現在統一由 GuardRuntime 從 `safety.control_frequency_hz` 算好注入到 config pool（見 [`guard_runtime.py:407-410`](../dam/runtime/guard_runtime.py)），callback 簽名宣告 `dt` 就拿到。

### 階段 3 ✅ — MotionGuard solver strategy 拆分

階段 1 移除的 QP / CBF 邏輯，以**可注入的 clamp aggregator** 形式回來，MotionGuard 維持 thin shell。設計選擇 **A + B + B**：

- **A — 專門檔案**：新增 [`dam/guard/aggregators/`](../dam/guard/aggregators/) package，QP 策略在 [`motion_qp.py`](../dam/guard/aggregators/motion_qp.py)。共用 pipeline（`dam/guard/pipeline.py`）完全不 import 它，也不認識 QP / CBF / MotionQPConstraint。
- **B — 小型 dataclass**：[`MotionQPConstraint`](../dam/guard/aggregators/motion_qp.py)（frozen）。**它只屬於 motion QP aggregator，不是通用 `CallbackResult` schema 的一部分** —— 框架不預設 constraint 語意（見 `feedback-no-predefined-semantics`）。除了 limits 本身，它還順帶 callback 從 runtime pool 拿到、aggregator 簽名拿不到的狀態（velocity 的 `q`/`dt`、CBF 的 `ee_pos`/`J_linear`），因為 aggregator 只收到 CLAMP results。
- **B — L1 callbacks 同時提供** fallback clamp（既有的 `clamped_action`）+ QP aggregator 可讀的 metadata（`metadata["motion_qp"]`）。

**`motion_qp_aggregator` fallback 行為**：
- 沒有任何 `metadata["motion_qp"]` → 退回 `sequential_clamp_aggregator`（即使 proxsuite 不在也不報錯）。
- 有 QP metadata 但 `proxsuite` 不可用 → **raise `RuntimeError`**，不 silent fallback（使用者明確選了 QP aggregator，solver 缺了就該大聲失敗）。
- QP solve 失敗（solver 回 None）→ 退回 sequential，確保機器人仍拿到安全 clamp。
- 多 boundary 合併：position box 取最嚴（upper 取 min、lower 取 max）、velocity 同理、workspace CBF rows 疊起來一起進 QP；`slack_weight` 取 max。

**`cbf_alpha` → `cbf_gamma`**（在 `workspace` callback）：
- 新名 `cbf_gamma`，語義為**直接的離散 CBF 衰減率 γ ∈ [0,1]**（不再 ×dt）。aggregator 呼叫既有 `qp_solver.workspace_cbf_constraints(..., cbf_alpha=γ, dt=1.0)` 讓 helper 直接用 γ，不複製 solver 底層。
- 舊名 `cbf_alpha` 收到時 `logger.warning` 並當作 `cbf_gamma`。
- `cbf_gamma` 超出 `[0,1]` → `ValueError`。

復用既有 [`dam/runtime/qp_solver.py`](../dam/runtime/qp_solver.py)（`available` / `solve_box_with_slack` / `workspace_cbf_constraints`），沒搬任何 solver 邏輯回 MotionGuard。

測試：新增 [`tests/unit/test_motion_qp_aggregator.py`](../tests/unit/test_motion_qp_aggregator.py)（7 cases 起，含 fallback / QP / CBF / proxsuite missing / alias validation），[`tests/unit/test_qp_solver.py`](../tests/unit/test_qp_solver.py) 兩個 phase 1 skip 的 inline-QP test 改成 `MotionGuard(clamp_aggregator=motion_qp_aggregator)` 的真實 path。`make test` 已全綠（commit `90d7caf`）。

### 階段 4 ✅ — L1/L2 職責切乾淨

`ExecutionGuard` 不再硬寫 L2 constraint 語意，只負責「跑 active L2 callback + timeout」。`max_speed` / `bounds` 改成 L2 callbacks。

已完成：
- 新增兩個 L2 callbacks（[`dam/boundary/callbacks/execution.py`](../dam/boundary/callbacks/execution.py)），都回 `CallbackResult`：
  - `task_joint_speed_limit`（取代內建 `max_speed`：整支手臂 velocity norm scalar cap，含 `use_degrees` + non-finite 防護）。
  - `task_workspace_bounds`（取代內建 `bounds`：EE 位置離開 box 即 REJECT，對比 L1 `workspace` 的 halt-clamp）。
- [`dam/guard/builtin/execution.py`](../dam/guard/builtin/execution.py) 移除 `max_speed` / `bounds` 內建檢查、`_cache_map`、`__init__`、`numpy` import；docstring 砍掉從未實作的 `max_force_n`。只剩 callback dispatch + timeout watchdog（timeout 是 node lifecycle，不是 stateless callback，故留在 guard）。
- Review 補強：`ExecutionGuard` dispatch 時固定 `expected_layer="L2"`，避免 L1/L3 callback 被錯掛進 L2 guard；對應測試鎖定 L3 force callback 在 ExecutionGuard 中會被忽略，應交給 HardwareGuard。
- 在 [`__init__`](../dam/boundary/callbacks/__init__.py) 與相容 shim [`builtin_callbacks.py`](../dam/boundary/builtin_callbacks.py) re-export 新 callback。
- `guard_runtime.py` 的 node-level `max_speed` 相容 shim 與 `merge_policy` 的 `take_min` 保留（`max_speed` 仍是合法 callback param）。
- 測試：[`test_execution_guard.py`](../tests/unit/test_execution_guard.py)（含「裸 params 無 callback → 被忽略」證明切乾淨）、[`test_execution_regression.py`](../tests/safety/test_execution_regression.py)、[`test_monitor_mode.py`](../tests/integration/test_monitor_mode.py)、[`test_guard_runtime.py`](../tests/integration/test_guard_runtime.py) 都改走 L2 callback boundary。

驗證：`make test` 全綠。

<details><summary>原始實作計劃（保留備查）</summary>

目標：`ExecutionGuard` 不再硬寫 L2 constraint 語意，只負責「跑 active L2 callback + timeout」。`max_speed` / `bounds` 這類 task-level 約束要做就寫 L2 callback。

實作計劃：

1. 在 [`dam/boundary/callbacks/`](../dam/boundary/callbacks/) 新增或整理 L2 callbacks：
   - `task_joint_speed_limit`：取代 ExecutionGuard 內建 `max_speed` 檢查。
   - `task_workspace_bounds`：取代 ExecutionGuard 內建 `bounds` 檢查。
   - callback 回 `CallbackResult.violate(...)` 或 legacy `False` 皆可，但建議新 callback 直接回 `CallbackResult`。
2. 修改 [`dam/guard/builtin/execution.py`](../dam/guard/builtin/execution.py)：
   - 移除 `max_speed` 檢查。
   - 移除 `bounds` 檢查。
   - 移除 `_cache_map`（如果只服務 max_speed degree conversion）。
   - 刪掉 docstring 中未實作的 `max_force_n`。
   - 保留 timeout 邏輯，因為 timeout 是 boundary node lifecycle，不是一般 callback constraint。
3. 更新 stackfile/測試建立 boundary 時的寫法：
   - 舊 `{params: {max_speed: ...}}` 測試改成 `{callback: task_joint_speed_limit, params: {...}}`。
   - 舊 `{params: {bounds: ...}}` 測試改成 `{callback: task_workspace_bounds, params: {...}}`。
4. 測試更新：
   - [`tests/unit/test_execution_guard.py`](../tests/unit/test_execution_guard.py)：刪掉「ExecutionGuard 自己懂 max_speed/bounds」的期待，改測 callback dispatch 與 timeout。
   - [`tests/safety/test_execution_regression.py`](../tests/safety/test_execution_regression.py)：改走 L2 callbacks。
   - [`tests/integration/test_monitor_mode.py`](../tests/integration/test_monitor_mode.py)：REJECT 場景用 L2 callback boundary，確認 monitor/enforce 語義不變。
5. 驗證：
   - `pytest tests/unit/test_execution_guard.py tests/safety/test_execution_regression.py tests/integration/test_monitor_mode.py -q`
   - 最後跑 `make test`。

完成標準：
- `ExecutionGuard.check()` 裡沒有 `max_speed` / `bounds` / force 這類 hard-coded constraint。
- 新增 L2 約束只需要新增 callback，不需要改 ExecutionGuard。
- monitor/enforce 行為維持一致：monitor 記錄 violation 但放行，enforce REJECT 時不送 sink。

</details>

### 階段 5 ✅ — 註冊一致性

四個 builtin 都用 `@dam.guard(layer="L*")` 在 class 上聲明（先前只有 HardwareGuard 這樣）。

已完成：
- [`OODGuard`](../dam/guard/builtin/ood.py)（L0）、[`MotionGuard`](../dam/guard/builtin/motion.py)（L1）、[`ExecutionGuard`](../dam/guard/builtin/execution.py)（L2）都加上 class decorator（沿用 HardwareGuard 的 `import dam` + `@dam.guard(layer=...)` pattern，無循環 import）。import class 即帶 canonical `_guard_layer` + injection slots。
- 修正 layer 漂移：先前測試把 `ExecutionGuard` 手動包成 L3，canonical 其實是 **L2**（execution = task 層）。
- [`builtin/__init__.py`](../dam/guard/builtin/__init__.py) `register_all` 保留顯式 kind→class + layer 字串（kind 映射仍需要；layer 與 class decorator 一致，雙重保險）。
- 移除「fixture 純為取得 layer 而手動包 decorator」的案例：[`test_ood_guard.py`](../tests/unit/test_ood_guard.py)、[`test_execution_guard.py`](../tests/unit/test_execution_guard.py)、[`test_execution_regression.py`](../tests/safety/test_execution_regression.py)、[`test_kinematic_regression.py`](../tests/safety/test_kinematic_regression.py) 改成直接回傳已 decorated 的 class。
- 保留手動 decorator 的地方（刻意）：自訂測試 subclass（`BrokenGuard` / `SpyExecutionGuard` / `_SyntheticGuard`）、專測 decorator/injection 的 [`test_injection.py`](../tests/unit/test_injection.py)，以及刻意用非 canonical layer 模擬多層 pipeline 的 [`test_phase2_pipeline.py`](../tests/integration/test_phase2_pipeline.py) / [`test_lerobot_runner.py`](../tests/unit/test_lerobot_runner.py)。
- 新增 contract test：[`test_guard_decision_contract.py`](../tests/unit/test_guard_decision_contract.py) `test_builtin_guards_declare_canonical_layer_on_class` 鎖定四個 guard 的 class-level layer。

<details><summary>原始實作計劃（保留備查）</summary>

目標：builtin guard 的 layer/name registration 不再靠測試或 runtime 手動 decorator 包一次；import class 時就帶有 canonical layer。

實作計劃：

1. 修改 builtin guard class：
   - `OODGuard` 用 `@dam.guard(layer="L0")`
   - `MotionGuard` 用 `@dam.guard(layer="L1")`
   - `ExecutionGuard` 用 `@dam.guard(layer="L2")`
   - `HardwareGuard` 確認現況並調整到同一風格
2. 檢查 [`dam/guard/builtin/__init__.py`](../dam/guard/builtin/__init__.py) 與 [`dam/decorators.py`](../dam/decorators.py)：
   - 避免重複 decorator 造成 registry duplicate 或 layer 被覆寫。
   - 保留外部自訂 guard 的 `@dam.guard(...)` backward compat。
3. 更新測試 helper：
   - 能直接 `g = MotionGuard()` 的地方就不要 `guard_decorator("L1")(MotionGuard)`。
   - 仍需要測 decorator 的地方保留小型專門測試。
4. 新增/更新 contract test：
   - builtin guard import 後 `get_layer()` / `_guard_layer` 正確。
   - `expected_decisions` 與 layer contract 一致。
   - 重複 import / register 不會 duplicate。
5. 驗證：
   - `pytest tests/unit/test_guard_decision_contract.py tests/unit/test_api.py tests/unit/test_injection.py -q`
   - 最後跑 `make test`。

完成標準：
- 測試與範例不再需要手動把 builtin guard 包 decorator 才能取得 layer。
- Guard registration 仍支援第三方自訂 guard。
- Phase 4 的 callback-based ExecutionGuard 不被註冊改動破壞。

</details>

### 階段 6 ✅ — OODGuard 拆 backend + L0 callback pipeline

`OODGuard` class 名稱保留，但演算法 dispatch 從散落字串改成 backend protocol + enum，並新增 L0 callback path 讓 stackfile 跟其他層一致地選 OOD 演算法。

已完成：
- **Backend 抽象** [`dam/guard/ood_backend.py`](../dam/guard/ood_backend.py)：`OODBackendKind` enum（含 alias + unknown→`ValueError`）、`OODBackend` Protocol、三個 adapter（`WelfordBackend`/`MemoryBankBackend`/`RealNVPFlowBackend`，延遲 import detector 避免循環）、`make_backend`。
- **Context** [`dam/guard/ood_context.py`](../dam/guard/ood_context.py)：持有共享 FeatureExtractor + backend cache，`features`/`raw_features`/`get_backend`/`load_backend`（per-`(key,path)` 載入一次，避免每 cycle disk IO）。
- **L0 callback** [`dam/boundary/callbacks/ood.py`](../dam/boundary/callbacks/ood.py)：單一 `ood_detector` 以 `backend` 選 `welford` / `memory_bank` / `normalizing_flow`，回 `CallbackResult`，metadata 帶 `score`/`threshold`/`backend`；主 backend 未 ready 時退回 Welford warmup。
- **OODGuard 變雙路徑** [`dam/guard/builtin/ood.py`](../dam/guard/builtin/ood.py)：有 active L0 callback → 走 `run_and_aggregate` pipeline（像 MotionGuard），用 guard 共享的 `OODContext`；無 callback → `_default_check` 跑 guard 自帶 detector（model-driven），dispatch 改用 `self._kind`（`OODBackendKind`）取代散落字串。temporal smoothing 仍在 guard 層套用（跨 frame state）。
- **相容**：保留 raw detector 屬性（`_extractor`/`_bank`/`_flow`/`_welford`/`_mean_train_nll`/`_backend_name`）—— 多個測試直接存取；`diagnostics()` keys 不變；`train`/`save`/`load` 不變。
- **Runtime**：[`guard_runtime.py`](../dam/runtime/guard_runtime.py) `_register_guard_if_new` 不再把 L0 OOD callback 名稱丟掉（現在 L0 boundary 也保留 callback 名稱，pipeline 才找得到）。`active_containers` 經 `RUNTIME_POOL_KEYS` 自動注入（與 MotionGuard 對稱）。
- **Hot path load**：`OODGuard.check()` 不再呼叫 `_maybe_load`；artifact loading 只留在 `prepare()` / `load()`，以及 callback path 的 `OODContext.load_backend` one-time cache。default path 仍保留舊 detector 行為，但只使用已 train/load 好的 state，沒有每 cycle disk IO。
- **`CallbackResult.ok`/`violate`** 加 optional `metadata`（純加法）。
- **相容 shim**：[`builtin_callbacks.py`](../dam/boundary/builtin_callbacks.py) re-export 新 L0 callbacks，舊 import path 與 catalog 一致。
- **測試**：新增 [`test_ood_backend.py`](../tests/unit/test_ood_backend.py)、[`test_ood_callbacks.py`](../tests/unit/test_ood_callbacks.py)；既有 OOD 測試全綠（含 default-path 相容），並鎖定 `check()` 不做 lazy load。

**取捨/偏離原計劃**：
- raw detector **沒有**搬進 backend，也沒從 `OODGuard` 拔掉 —— 既有測試直接 import/存取 `MemoryBank`/`RealNVPFlow`/`_WelfordStats` 與 guard 私有屬性，為「不回歸」保留。backend adapter 是包一層，pipeline path 用它，default path 仍直接用 raw detector。
- `_maybe_load` 已離開 `check()` hot path；舊式 no-callback default path 若需要 artifact，呼叫端需先走 `prepare()` 或 `load()`。callback path 則由 `OODContext.load_backend` 以 `(key,path)` cache 確保只載入一次；更完整的 runtime preflight hook 可留到下一輪，但 hot path 不再每 cycle load。
- Stackfile 僅以 `ood_detector` 宣告 L0 OOD；算法是參數而不是額外 callback 名稱。

<details><summary>原始實作計劃（保留備查）</summary>

> 建議放在 Phase 4/5 後。理由：OOD 涉及 model state、training、load/save、diagnostics，風險比 ExecutionGuard cleanup 高。先把 guard registration 與 callback dispatch 邊界收乾淨，再切 ML backend 會比較不混。

目標：`OODGuard` class 名稱保留，但內部 detector 從 guard 本體拆成 backend。`MemoryBank` / `RealNVPFlow` / `Welford` 抽成 `OODBackend` Protocol；backend 字串改 Enum；`_maybe_load` 從 hot path 拔到 init/preflight。

實作計劃：

1. 新增 backend contract：
   - 位置建議：`dam/guard/ood_backend.py` 或 `dam/guard/builtin/ood_backend.py`。
   - 定義 `OODBackend` Protocol，至少包含：
     - `score(obs_or_features) -> float`
     - `train(samples/features) -> None`
     - `diagnostics() -> dict[str, Any]`
     - `save(path) / load(path)`（若現有 backend 支援）
   - 定義 `OODBackendKind(Enum)`：`WELFORD`, `MEMORY_BANK`, `NORMALIZING_FLOW`。
2. 把現有 detector state 包成 backend：
   - `WelfordBackend`
   - `MemoryBankBackend`
   - `RealNVPFlowBackend`
   - feature extractor 可以先留在 `OODGuard`，也可以包到 backend；第一版建議留在 guard，降低改動。
3. 修改 [`dam/guard/builtin/ood.py`](../dam/guard/builtin/ood.py)：
   - `backend` 參數接受 Enum 或字串 alias，字串轉 Enum 並保留相容 warning。
   - `check()` hot path 不再 `_maybe_load`。
   - load/model path validation 移到 `__init__` 或 runtime preflight。
   - `OODGuard` 只做：extract features → backend score → smoothing/consecutive logic → GuardResult。
4. 更新 training / diagnostics 呼叫：
   - 現有 `train(...)` 委派到 backend。
   - `diagnostics()` 聚合 guard-level smoothing state + backend diagnostics。
5. 測試更新：
   - [`tests/unit/test_ood_guard.py`](../tests/unit/test_ood_guard.py)
   - [`tests/unit/test_ood_memory_bank.py`](../tests/unit/test_ood_memory_bank.py)
   - [`tests/unit/test_ood_normalizing_flow.py`](../tests/unit/test_ood_normalizing_flow.py)
   - [`tests/safety/test_ood_regression.py`](../tests/safety/test_ood_regression.py)
   - 新增 backend protocol/enum tests。
6. 驗證：
   - `pytest tests/unit/test_ood_guard.py tests/unit/test_ood_memory_bank.py tests/unit/test_ood_normalizing_flow.py tests/safety/test_ood_regression.py -q`
   - 最後跑 `make test`。

完成標準：
- `OODGuard.check()` 不做 lazy load。
- backend 選擇不再散落字串比較。
- `OODGuard` 對外 class name、decision semantics、diagnostics key 盡量維持相容。
- model load/save/training lifecycle 有明確測試。

</details>

## 不在這份 refactor 內

- 不換 pydantic（stdlib dataclass 夠）
- 不改 `GuardResult` 結構
- 不改 boundary YAML schema（alias 在 schema 層接受 + warn）
- 不動 `merge_policy` / `injection.pool.RUNTIME_POOL_KEYS` 名稱

## 未解決 / 後續再議

- ~~**L1 多個 CLAMP 結果該怎麼預設融合？**~~ 已定案：預設仍是 sequential；想要 joint fusion 的使用者注入 `motion_qp_aggregator`（階段 3 完成）。
- ~~**OODGuard 要不要 callback 化？**~~ 已定案：stackfile 對外用 L0 callback 選演算法；`OODGuard` 保留 class name 與 legacy no-callback detector path 作相容層。
- **遷移期 backward compat**：舊 YAML 還在用 `joint_position_limits` 走舊路徑，需要時間視窗讓使用者改寫。考慮 0.6.0 release note 列 deprecation。

## 檔案相關性快查

| 主題 | 檔案 |
|---|---|
| Guard ABC | [`dam/guard/base.py`](../dam/guard/base.py) |
| Guard 註冊 | [`dam/guard/builtin/__init__.py`](../dam/guard/builtin/__init__.py), [`dam/decorators.py`](../dam/decorators.py) |
| Callback registry | [`dam/registry/callback.py`](../dam/registry/callback.py), [`dam/boundary/callbacks/_registry.py`](../dam/boundary/callbacks/_registry.py) |
| Injection | [`dam/injection/resolver.py`](../dam/injection/resolver.py), [`dam/injection/static.py`](../dam/injection/static.py), [`dam/injection/pool.py`](../dam/injection/pool.py) |
| 既有 callback dispatch | [`dam/guard/callbacks.py`](../dam/guard/callbacks.py) |
| QP / CBF solver helper | [`dam/runtime/qp_solver.py`](../dam/runtime/qp_solver.py) |
| Clamp aggregators（可注入） | [`dam/guard/aggregators/`](../dam/guard/aggregators/) |
| OOD backend / context | [`dam/guard/ood_backend.py`](../dam/guard/ood_backend.py), [`dam/guard/ood_context.py`](../dam/guard/ood_context.py) |
| OOD L0 callbacks | [`dam/boundary/callbacks/ood.py`](../dam/boundary/callbacks/ood.py) |
| Builtin guards | [`dam/guard/builtin/`](../dam/guard/builtin/) |
| Boundary callbacks | [`dam/boundary/callbacks/`](../dam/boundary/callbacks/) |
