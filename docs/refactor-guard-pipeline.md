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

四個 guard 都跑同一個 pipeline。差別只在：
- 各 guard 可注入自己的 aggregator（MotionGuard 之後可以接 QP aggregator）
- OODGuard 帶 ML state，遷移要等到階段 4

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
- 階段 3：MotionGuard `clamp_aggregator` 抽出「QP fusion strategy」作為內建 aggregator 之一（之前的 inline QP path 砍掉了，要用 QP 的使用者目前需要自己寫 aggregator）。
- 階段 4：OODGuard 還是 model-driven、不走 callback pipeline。如果要對齊可以拆 backend 後讓 callback 跑 backend，但要小心 model state 的 lifecycle。

### 階段 2 ✅ — 統一 dt 來源（隨階段 1 自然落地）

MotionGuard 自己從 `obs.timestamp` 推 `effective_dt` 那段已隨著 MotionGuard 重寫一併刪除。`dt` 現在統一由 GuardRuntime 從 `safety.control_frequency_hz` 算好注入到 config pool（見 [`guard_runtime.py:407-410`](../dam/runtime/guard_runtime.py)），callback 簽名宣告 `dt` 就拿到。

### 階段 3 — MotionGuard solver strategy 拆分

階段 1 完成後，QP / box-clamp / CBF 從 MotionGuard 抽出來成可注入 aggregator。`cbf_alpha` 改名 `cbf_gamma`（語義改為直接 γ ∈ [0,1]）。proxsuite 不可用時 build time 報錯，不再 silent fallback。

### 階段 4 — OODGuard 拆 backend

`MemoryBank` / `RealNVPFlow` / `Welford` 抽成 `OODBackend` Protocol。backend 字串改 Enum。`_maybe_load` 從 hot path 拔到 preflight。**class 名稱保留 `OODGuard`**。

### 階段 5 — L1/L2 職責切乾淨

階段 1 之後自然落地：`max_speed` / `bounds` 不再寫死在 ExecutionGuard，要做就寫一個 L2 callback。docstring 中 `max_force_n` 從未實作，刪掉。

### 階段 6 — 註冊一致性

四個 builtin 都用 `@dam.guard(layer="L*")` 在 class 上聲明（目前只有 HardwareGuard 這樣）。

## 不在這份 refactor 內

- 不換 pydantic（stdlib dataclass 夠）
- 不改 `GuardResult` 結構
- 不改 boundary YAML schema（alias 在 schema 層接受 + warn）
- 不動 `merge_policy` / `injection.pool.RUNTIME_POOL_KEYS` 名稱

## 未解決 / 後續再議

- **L1 多個 CLAMP 結果該怎麼預設融合？** 目前傾向 sequential，但 sequential 對 QP-friendly 約束會錯失 fusion 機會。短期：sequential；長期：MotionGuard 提供「QP aggregator」作為使用者可選的進階 aggregator。
- **OODGuard 的 callback 化怎麼設計？** L0 callback 拿 obs 計算 OOD score 是合理的，但 model 載入 / training 不適合放 callback。可能 OODGuard 維持非 pipeline 路徑，僅 `expected_decisions` 對齊。
- **遷移期 backward compat**：舊 YAML 還在用 `joint_position_limits` 走舊路徑，需要時間視窗讓使用者改寫。考慮 0.6.0 release note 列 deprecation。

## 檔案相關性快查

| 主題 | 檔案 |
|---|---|
| Guard ABC | [`dam/guard/base.py`](../dam/guard/base.py) |
| Guard 註冊 | [`dam/guard/builtin/__init__.py`](../dam/guard/builtin/__init__.py), [`dam/decorators.py`](../dam/decorators.py) |
| Callback registry | [`dam/registry/callback.py`](../dam/registry/callback.py), [`dam/boundary/callbacks/_registry.py`](../dam/boundary/callbacks/_registry.py) |
| Injection | [`dam/injection/resolver.py`](../dam/injection/resolver.py), [`dam/injection/static.py`](../dam/injection/static.py), [`dam/injection/pool.py`](../dam/injection/pool.py) |
| 既有 callback dispatch | [`dam/guard/callbacks.py`](../dam/guard/callbacks.py) |
| QP / CBF（之後拆出） | [`dam/runtime/qp_solver.py`](../dam/runtime/qp_solver.py) |
| Builtin guards | [`dam/guard/builtin/`](../dam/guard/builtin/) |
| Boundary callbacks | [`dam/boundary/callbacks/`](../dam/boundary/callbacks/) |
