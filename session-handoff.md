# session-handoff.md — 會話交接摘要

> 最後更新: 2026-05-29 (L1/L2 CBF Unification + Pool Forwarding)

## 本輪工作

### L1 CBF Unification

所有 L1 kinematics callbacks 現在都走 QP CBF：

**L1 callback 全景**（6 個）:
- `joint_position_limits` → QPTerm(upper, lower)
- `joint_velocity_limit` → QPTerm(upper, lower) + 加速度限制
- `workspace` → QPTerm(A, b) via workspace box CBF
- `keep_out_zone` (NEW) → QPTerm(A, b) via sphere CBF（一個不等式/sphere）
- `orientation_limit` (NEW) → QPTerm(A, b) via tilt CBF（angular Jacobian）

**EE precomputation pool**:
`pool["ee_pos"]`, `pool["ee_rot"]`, `pool["J_linear"]`, `pool["J_angular"]`（新增）

**所有 callback 的 fallback**:
- 有 Jacobian → CBF 線性化 → QPTerm → QP solver 融合
- 無 Jacobian → halt（凍結 action，原有行為）

### Unified Pool Forwarding

解決了 guards 自建 pool 丟掉預計算 FK 的問題：
- `ValidationContext` 帶 `config_pool`
- `_build_runtime_pool` 把 config_pool（dt 等）merge 進 runtime pool
- MotionGuard / ExecutionGuard 直接轉傳 engine 的 pool，不再自建
- 結果：L1/L2 callbacks 都能讀到 `ee_pos`, `J_linear`, `J_angular`, `dt`

### Review Fixes
- sphere center zero-normal → 用 arbitrary escape direction [1,0,0]
- dt hardcoded 0.02 → 三個 CBF callback 從 pool 接收 actual dt
- pool forwarding → 統一為 engine builds once, guards forward

**下一步**:
- 實機驗證 CBF 參數（cbf_alpha, slack_weight）在 SO-101 上的表現
- keep-out box（非凸，需要不同策略）
- `base_geofence` 考慮是否值得回歸（mobile base 場景）
- CBF 三個 callback 的 boilerplate 可抽 helper（halt_fallback + qp_result 約 25 行 × 3）

### GuardRuntime 拆分 (1726 → ~1037 lines)

Composition pattern: GuardRuntime creates helper instances in `__init__`, delegates via `self._ctx_sm`, `self._telemetry`, `self._hot_reload`.

- **`_stackfile_builder.py`** (296 lines) — `from_stackfile()`, `from_config()`, boundary construction classmethods
- **`_context_state_machine.py`** (221 lines) — Stack-based push/pop context, fallback routing, worst-reject selection
- **`_cycle_telemetry.py`** (284 lines) — Loopback recording, frame hub bridging, failure harvest, latency logging
- **`_hot_reload.py`** (141 lines) — Thread-safe double-buffer config swap, config pool building

Zero test modifications: backward-compat delegator properties/methods (~75 lines) on GuardRuntime preserve existing test code.

### Legacy Shim Elimination

- `dam/guard/callbacks.py` **deleted** — the `evaluate_boundary_callbacks` shim that wrapped pipeline results into `(saw_callback, GuardResult|None)`
- `ExecutionGuard` now calls `run_callbacks` + `aggregate` directly
- `HardwareGuard` now calls `run_callbacks` directly with manual worst-result selection + flat metadata merge for PASS path

### Injection System Assessment

Investigated whether the two injection systems are redundant. Conclusion: **they are NOT**.
- **Static injection** (`precompute_injection`): binds config-pool params to `guard.check()` signatures
- **Pipeline injection** (`resolve_kwargs`): binds runtime-pool + config-pool params to boundary callback signatures
- Different consumers, different lifecycles — no unification needed.

### Tests + Features

- Added 7 workspace callback unit tests (commit `ff77cad`)
- **Percentile-based OOD threshold** — stores training score percentiles (90, 95, 97.5, 99, 99.5, 99.9) in model checkpoints. New `threshold_percentile` param in `ood_detector` callback selects empirical percentile cutoff. More robust than Gaussian mean+σ·std for skewed NLL distributions (commit `107a7ed`)
- **`action_smooth` L1 callback** — EMA-based oscillation damping. Configurable alpha (0.01-1.0). Addresses micro-oscillation from noisy policy outputs without modifying the model (commit `38d243a`)

## 當前已驗證

- **`make test` ALL PASSED**: 666 unit + 28 integration + 43 safety + 2 property + 55 Rust + 109 frontend = 0 failures
- pre-commit hooks: all passed (ruff + mypy + format)

## Commits (本輪)

- `f68501c` refactor: extract stackfile factory from GuardRuntime into _stackfile_builder.py
- `36187e7` refactor: extract context state machine from GuardRuntime into _context_state_machine.py
- `949e71f` refactor: extract cycle telemetry from GuardRuntime into _cycle_telemetry.py
- `e488008` refactor: extract hot reload from GuardRuntime into _hot_reload.py
- `81cd86a` refactor: eliminate legacy callback shim — guards call pipeline directly
- `ff77cad` test: add workspace callback unit tests (7 cases)
- `107a7ed` feat: add percentile-based OOD threshold strategy
- `38d243a` feat: add action_smooth L1 callback for EMA-based oscillation damping
- `7bba34f` fix: update monkeypatch target after GuardRuntime telemetry extraction
- `84745fa` refactor: consolidate L1 kinematics — acceleration limit + remove weak callbacks
- `7f39640` docs: update L1 callback documentation after consolidation
- `b3dd856` refactor: QP mandatory + delete weak L1 callbacks
- `73c8ed6` docs: align docs, examples, templates with QP-mandatory L1
- `1300d1e` feat: EE precomputation — FK once per cycle, shared via pool
- `6246d33` feat: L2 gripper callback reads pre-computed ee_pos from pool
- `239c764` refactor: MotionQPConstraint → QPTerm — generic box + linear inequality

## 待後續處理

### P0 — Spawned tasks (可獨立完成)
- Stackfile validation: warn when boundary references channel that no source provides (needs channel registry design first)
- Stackfile examples for 6 missing L1 callbacks (low priority)

### P1 — 功能
- ~~Vision OOD 閾值校準（percentile-based）~~ ✅ Done
- ~~機器人微振盪（action smoothing）~~ ✅ Done (`action_smooth` callback)
- ~~MCAP 回讀 Risk Log~~ ✅ Already implemented (backend: `/api/risk-log/mcap/{filename}`, frontend: MCAP viewer page)
- `make record` 端到端硬體驗證（needs real hardware）

## 命令速查

```bash
make setup                              # 首次安裝（含 rerun）
make dev                                # 開發模式
make test                               # 完整測試
make record                             # 安全錄製（讀 safety.yaml）
make lint                               # linter
python -m pytest tests/unit/ -x -q      # 快速 unit test
dam validate examples/stackfiles/*.yaml # 驗證所有 stackfile
dam callbacks                           # 列出內建安全檢查
```
