# session-handoff.md — 會話交接摘要

> 最後更新: 2026-05-29 (GuardRuntime Decomposition + Legacy Shim Elimination)

## 本輪工作

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
