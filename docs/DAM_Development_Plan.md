# DAM 開發計畫書

> Detachable Action Monitor — 分階段開發計畫與測試規範
> 2026 年 4 月

---

## 0. 開發總原則

### 0.1 軟體工程原則

本專案遵循以下核心原則：

- **每個 Phase 產出可運行、可測試的系統**，才進入下一個 Phase。不允許「先寫完全部再測」。
- **測試先行（Test-First）**：凍結資料型別 → 寫測試 → 實現邏輯 → 通過測試。Safety regression test 永遠先於功能代碼。
- **最小依賴引入**：Phase 1 純 Python 無任何硬體依賴；Phase 2 才引入 Rust/PyO3；Phase 4 才引入模擬器。每個 Phase 的依賴範圍是硬邊界。
- **CI 門檻（CI Gate）**：所有 PR 必須通過 CI 全流程才能合併。不存在「先 merge 再修」。

### 0.2 程式碼品質要求

**Python（Control Plane — `dam/` 目錄）**

| 項目 | 要求 |
|---|---|
| 型別提示 | 所有公開接口強制 strict type hints，使用 `mypy --strict` 檢查 |
| 格式化 | `ruff format`（或 `black`），CI 中強制執行 |
| Lint | `ruff check`（或 `flake8`），零警告 |
| 資料結構 | 核心型別用 `@dataclass(frozen=True)`，不可變優先 |
| 異常處理 | 守衛 `check()` 內部的異常由框架統一 `try/except`，不允許守衛吞掉異常 |
| import 副作用 | `@dam.guard` / `@dam.callback` 在 import 時完成簽名檢查；不允許其他 import 時副作用 |

**Rust（Data Plane — `dam-rs/` 目錄）**

| 項目 | 要求 |
|---|---|
| 格式化 | `cargo fmt`，CI 強制 |
| Lint | `cargo clippy -- -D warnings`，零警告 |
| `unsafe` | 僅限 PyO3 FFI 邊界和零拷貝緩衝區。每個 `unsafe` 塊必須附帶 `// SAFETY:` 註解說明為何安全 |
| 測試 | `cargo test`，Rust 側的單元測試覆蓋所有 bus 操作和 WatchdogTimer 邏輯 |
| PyO3 接口 | 最小暴露面——只暴露 Python 必須調用的函數，內部 Rust 實現不暴露 |

**Stackfile / YAML**

| 項目 | 要求 |
|---|---|
| Schema 驗證 | `dam validate --stack <file>` 必須通過，CI 對所有範例 Stackfile 執行驗證 |
| 範例文件 | 每個 Phase 附帶至少一個完整的範例 Stackfile，作為文件也作為測試輸入 |

### 0.3 Git 工作流

#### 分支策略

| 分支 | 說明 |
|---|---|
| `main` | 主幹整合分支。所有穩定功能與修復最終落地於此。Official release tags 直接從 `main` 切出。**保護分支，不可直接推送。** |
| `dev` | 持續開發分支。所有 `issue/<num>` 分支基於此建立，完成後透過 PR 合回 `dev`，再從 `dev` 往 `main` 整合。 |
| `issue/<num>` | 對應特定 GitHub Issue 的功能或修復分支，從 `dev` 分出，完成後透過 PR 合回 `dev`。 |
| `release/[data-plane\|control-plane]/vX.Y.<Z+1>` | 維護分支，用於舊版本的緊急熱修復（hotfix）。從現有版本 Tag 切出，末次 commit 打新 Tag。相關修復應 cherry-pick 或 merge 回 `main`。未來亦可用於支援 pre-release 階段。 |

#### Tag 慣例

DAM 包含兩個可獨立發布的元件，使用不同 Tag 前綴觸發 CI/CD：

| 元件 | Tag 前綴 | 說明 |
|---|---|---|
| Python Control Plane（`dam/`） | `control-plane/vX.Y.Z` | Python 守衛框架、邊界評估、適配器 |
| Rust Data Plane（`dam-rs/`） | `data-plane/vX.Y.Z` | ObservationBus、ActionBus、WatchdogTimer、E-Stop |

#### 開發流程

1. 從 `dev` 建立 `issue/<num>` 分支
2. 實作功能，確保附帶對應測試
3. 開 PR 到 `dev`，CI 全通過後合併
4. 累積足夠功能後，從 `dev` 開 PR 到 `main`
5. Release 時在 `main` 打對應 Tag（`control-plane/vX.Y.Z` 或 `data-plane/vX.Y.Z`）

#### Commit 格式

`<type>(<scope>): <description>`

- **type**：`feat`, `fix`, `test`, `refactor`, `docs`, `ci`
- **scope**：`core`, `guard`, `boundary`, `adapter`, `rust`, `stackfile`, `cli`
- **範例**：`feat(guard): implement MotionGuard L2 with joint limit clamping`

#### PR 規範

- 每個 PR 必須附帶測試，且 CI 全通過才能合併
- PR 標題格式同 Commit 格式
- PR 描述需包含：變更摘要、測試方法、相關 Issue 編號

### 0.4 CI/CD 管線設計

```yaml
# .github/workflows/ci.yml 概念結構
name: DAM CI

on: [push, pull_request]

jobs:
  # ── Python 檢查 ─────────────────────────────────────
  python-lint:
    steps:
      - ruff format --check dam/
      - ruff check dam/
      - mypy --strict dam/

  python-unit:
    steps:
      - pytest tests/unit/ -v --tb=short

  python-integration:
    steps:
      - pytest tests/integration/ -v --tb=short

  safety-regression:
    steps:
      - pytest tests/safety/ -v --tb=short -x  # -x: 第一個失敗立即停止
    # 此 job 失敗會阻擋合併（required check）

  # ── Rust 檢查 ────────────────────────────────────────
  rust-check:
    steps:
      - cargo fmt --check
      - cargo clippy -- -D warnings
      - cargo test

  # ── Stackfile 驗證 ───────────────────────────────────
  stackfile-validate:
    steps:
      - dam validate --stack examples/*.yaml

  # ── Nightly（不阻擋合併，但追蹤趨勢）─────────────────
  nightly-fuzz:
    schedule: cron('0 3 * * *')
    steps:
      - pytest tests/property/ -v --hypothesis-seed=random
```

**CI 門檻等級**：

| 檢查項目 | 觸發時機 | 是否阻擋合併 |
|---|---|---|
| Python lint + type check | 每次 push/PR | **是** |
| Python unit test | 每次 push/PR | **是** |
| Python integration test | 每次 push/PR | **是** |
| Safety regression test | 每次 push/PR | **是（最高優先級）** |
| Rust fmt + clippy + test | 每次 push/PR（Phase 2 起） | **是** |
| Stackfile schema 驗證 | 每次 push/PR | **是** |
| Property/fuzz test | 每日凌晨 | 否（追蹤趨勢） |
| Hardware-in-the-loop | 手動觸發（Release 前） | 否（Release gate） |

---

### 0.5 開發環境（Docker Compose）

本專案以 **Docker Compose** 作為標準開發環境，取代 Python 虛擬環境，確保所有開發者與 CI 環境一致。

#### 目錄結構

```
docker/
├── Dockerfile.dev     ← 互動式開發容器（含 dev 工具）
└── Dockerfile.test    ← CI 測試容器（入口點為 pytest）
docker-compose.yml     ← 服務定義
pyproject.toml         ← 工具鏈設定（ruff / mypy / pytest）的唯一來源
```

#### 服務說明

| 服務 | 用途 | 指令 |
|---|---|---|
| `dev` | 互動式開發 shell，掛載專案目錄 | `docker compose run dev` |
| `test` | 完整測試套件（排除硬體標記） | `docker compose run test` |
| `lint` | 格式檢查 + 型別檢查 | `docker compose run lint` |

#### Phase 限制

- **Phase 1**：僅 Python 容器，無 Rust 工具鏈
- **Phase 2 起**：加入 `rust-builder` 服務，包含 `rustup` + `cargo` + `maturin`（PyO3 建構工具）
- **硬體測試**（`@pytest.mark.hardware`）：在 CI 中永遠跳過（`-m "not hardware"`），只在有實體硬體的環境手動執行

#### 常用指令

```bash
# 啟動互動式開發 shell
docker compose run dev

# 僅跑 unit tests
docker compose run test pytest tests/unit/ -v

# 僅跑 safety regression
docker compose run test pytest tests/safety/ -v -x

# 格式化 + lint + 型別檢查
docker compose run lint

# 進入開發容器後跑完整 CI 流程
docker compose run dev bash -c "ruff format dam/ tests/ && ruff check dam/ && mypy --strict dam/ && pytest tests/ -m 'not hardware'"
```

---

## 1. Phase 1 — Core Safety Engine

> 純 Python，無硬體依賴，完全可在 CI 中測試

### 1.0 Phase 1 模組依賴圖

```
[1A] Core Data Types ─────────────┐
                                   ├──→ [1D] Guard ABC + Decision
[1B] BoundaryNode + Constraint ───┤     │
                                   │     ▼
[1C] CallbackRegistry ────────────┼──→ [1E] BoundaryContainers
                                   │     │
[1F] FallbackRegistry ────────────┤     │
                                   │     ▼
[1G] InjectionPool + Resolver ────┼──→ [1H] GuardRuntime (sequential)
                                   │     │
                                   │     ▼
                                   ├──→ [1I] MotionGuard (L2)
                                   │     │
                                   │     ▼
                                   └──→ [1J] Stackfile Parser
                                         │
                                         ▼
                                   [1K] Testing Utilities
                                         │
                                         ▼
                                   [EXIT] dam.step() 全週期 mock 測試通過
```

### 1A — 核心資料型別（凍結第一）

**開發內容**：

定義並凍結所有核心 dataclass，後續所有 Phase 依賴這些型別。一旦凍結，只能新增欄位（附帶預設值），不可修改已有欄位語義。

| 模組 | 型別 | 要點 |
|---|---|---|
| `dam/types/observation.py` | `Observation` | timestamp, joint_positions, joint_velocities, end_effector_pose, force_torque, images, metadata |
| `dam/types/action.py` | `ActionProposal`, `ValidatedAction` | ActionProposal 含 confidence + policy_name；ValidatedAction 含 was_clamped + original_proposal 回溯 |
| `dam/types/result.py` | `GuardResult`, `Decision(IntEnum)`, `GuardDecision(IntEnum)` | GuardResult 含 factory 方法：`.pass_()`, `.reject(reason)`, `.clamp(action)`, `.fault(exc, source)` |
| `dam/types/risk.py` | `RiskLevel(IntEnum)`, `CycleResult` | CycleResult 是 `dam.step()` 的完整返回值 |

**測試**：

| 測試類型 | 內容 | 文件 |
|---|---|---|
| Unit | 所有 dataclass 的建構、序列化/反序列化、factory 方法 | `tests/unit/test_types.py` |
| Unit | Decision IntEnum 排序正確性（PASS < CLAMP < REJECT < FAULT） | `tests/unit/test_types.py` |
| Unit | 凍結性驗證：`frozen=True` 的 dataclass 不可修改欄位 | `tests/unit/test_types.py` |

**完成標準**：所有型別定義完成，unit test 全通過，`mypy --strict` 通過。

---

### 1B — 邊界節點與約束

**開發內容**：

| 模組 | 內容 |
|---|---|
| `dam/boundary/node.py` | `BoundaryNode`：node_id, constraints dict, fallback, timeout_sec |
| `dam/boundary/constraint.py` | `BoundaryConstraint`：max_speed, bounds, upper_limits, lower_limits, callback list |

約束值在載入時預解析為原生型別（float, np.ndarray），不在每週期重新解析。

**測試**：

| 測試類型 | 內容 |
|---|---|
| Unit | BoundaryNode 建構、約束條件存取 |
| Unit | 約束值預解析：字串 → float/ndarray 正確轉換 |
| Unit | bounds 格式驗證（3D 邊界框） |

---

### 1C — CallbackRegistry

**開發內容**：

| 模組 | 內容 |
|---|---|
| `dam/registry/callback.py` | `CallbackRegistry`：register(name, fn)、get(name)、list_all() |
| `dam/decorators.py` | `@dam.callback("name")` 裝飾器——import 時執行 `inspect.signature` 並快取 |

M:N 映射：一個 callback 可被多個 YAML 節點引用，一個節點可引用多個 callback。

**測試**：

| 測試類型 | 內容 |
|---|---|
| Unit | 註冊、查找、重複名稱衝突拋錯 |
| Unit | 簽名檢查：未知參數名在 import 時拋 ValueError |
| Unit | 簽名快取驗證：同一函式只 inspect 一次 |

---

### 1D — Guard ABC + Decision + DecisionAggregator

**開發內容**：

| 模組 | 內容 |
|---|---|
| `dam/guard/base.py` | `Guard` ABC：check(**kwargs) → GuardResult, get_layer(), get_name(), on_violation() |
| `dam/guard/aggregator.py` | `DecisionAggregator`：aggregate(List[GuardResult]) → GuardResult，使用 max(IntEnum) |
| `dam/guard/layer.py` | `GuardLayer(IntEnum)`：L0=0, L1=1, L2=2, L3=3, L4=4 |
| `dam/decorators.py` | `@dam.guard(layer="L2")` 裝飾器——在裝飾時將字串轉為 GuardLayer IntEnum |

**測試**：

| 測試類型 | 內容 |
|---|---|
| Unit | DecisionAggregator：REJECT > CLAMP > PASS 優先級正確 |
| Unit | 空結果列表處理（應 PASS） |
| Unit | FAULT 被視為 REJECT |
| Unit | `@dam.guard` 裝飾器：layer 字串轉 IntEnum 正確，無效字串拋錯 |
| Safety | 含 REJECT 的任何組合，聚合結果必須為 REJECT |

---

### 1E — BoundaryContainer（三種類型）

**開發內容**：

| 模組 | 內容 |
|---|---|
| `dam/boundary/container.py` | `BoundaryContainer` ABC：evaluate(), get_active_node(), advance(), reset(), snapshot(), restore() |
| `dam/boundary/single.py` | `SingleNodeContainer`：單節點，永不切換 |
| `dam/boundary/list_container.py` | `ListContainer`：有序節點清單，依序切換 |
| `dam/boundary/graph.py` | `GraphContainer`：帶條件邊的有向圖，支持循環 |

**測試**：

| 測試類型 | 內容 |
|---|---|
| Unit | SingleNodeContainer：evaluate 永遠返回同一節點約束 |
| Unit | ListContainer：advance 按順序切換、到末尾行為、reset 回到起點 |
| Unit | GraphContainer：條件分支正確路由、循環不死鎖 |
| Unit | snapshot/restore 狀態一致性（暫停後恢復回同一節點） |
| Property | Hypothesis：隨機 advance 序列後 snapshot → restore → 狀態一致 |

---

### 1F — FallbackRegistry + 內建策略

**開發內容**：

| 模組 | 內容 |
|---|---|
| `dam/fallback/registry.py` | `FallbackRegistry`：register(strategy)、execute_with_escalation(name, ctx, bus) |
| `dam/fallback/builtin.py` | 內建策略：`emergency_stop`, `hold_position`, `safe_retreat` |
| `dam/fallback/chain.py` | 升級鏈解析：啟動時解析 `escalates_to` 為物件指針鏈結 |

升級鏈在啟動時解析為物件指針串列，運行時只需走指針。最終回退（terminal fallback）永遠是 `emergency_stop`。

**測試**：

| 測試類型 | 內容 |
|---|---|
| Unit | 升級鏈按順序執行 |
| Unit | 終端回退永遠是 emergency_stop（不可跳過） |
| Unit | 未知策略名稱拋 ValueError |
| Safety | 回退鏈中任何一環失敗 → 直接升級到 emergency_stop |

---

### 1G — InjectionPool + Resolver

**開發內容**：

| 模組 | 內容 |
|---|---|
| `dam/injection/pool.py` | `RuntimePool`（Rust 側每週期更新）、`ConfigPool`（Stackfile 靜態） |
| `dam/injection/resolver.py` | `InjectionResolver`：register(fn, valid_keys)、call(fn, pool)、build_pool() |
| `dam/injection/static.py` | 啟動時預分割邏輯：`_static_kwargs`（frozen dict）、`_runtime_keys`（list） |

**關鍵行為**：
- import 時：`inspect.signature()` 快取為 `list[str]`
- 啟動時：根據 `sig_keys` 與 `RUNTIME_POOL_KEYS` 交集，預分割為 `_static_kwargs` 和 `_runtime_keys`
- 每週期：只做 `{k: runtime_pool[k] for k in guard._runtime_keys}`（2–4 次 dict 查找）

**測試**：

| 測試類型 | 內容 |
|---|---|
| Unit | 未知參數名 → import 時拋 ValueError |
| Unit | _static_kwargs 正確取自 config_pool |
| Unit | _runtime_keys 只包含 RUNTIME_POOL_KEYS 中的鍵 |
| Unit | 每週期調用不觸發 inspect（驗證快取有效） |
| Unit | 名稱衝突時 runtime pool 覆蓋 config pool |

---

### 1H — GuardRuntime（循序版，暫無 DAG）

**開發內容**：

| 模組 | 內容 |
|---|---|
| `dam/runtime/guard_runtime.py` | `GuardRuntime`：validate(obs, action), step(), run(task), start_task(), pause_task(), resume_task(), stop_task() |

Phase 1 為循序執行版本（逐一調用每個守衛的 check()）。Stage DAG 並行版在 Phase 3 實現。

**關鍵行為**：
- `validate()` 內部用 try/except 包裝每個 `guard.check()`，異常 → `GuardResult.fault()`
- Fault 在 aggregator 中被視為 REJECT
- `step()` 完成 sense → validate → act 一個完整週期，返回 `CycleResult`

**測試**：

| 測試類型 | 內容 |
|---|---|
| Unit | 單守衛 PASS → ValidatedAction 正確傳遞 |
| Unit | 單守衛 REJECT → fallback 被觸發 |
| Unit | 守衛異常 → FAULT → 視為 REJECT |
| Integration | 多守衛循序執行 + DecisionAggregator 聚合結果正確 |
| Integration | start_task → step 多次 → stop_task 生命週期完整 |
| Safety | 任何守衛拋異常，動作不可到達（ValidatedAction 為 None） |

---

### 1I — MotionGuard（L2）— 第一個也是最重要的守衛

**開發內容**：

| 模組 | 內容 |
|---|---|
| `dam/guard/builtin/motion.py` | `MotionGuard`：關節限制 clamp、速度限制、加速度限制、工作空間邊界 REJECT |

**檢查邏輯**：
1. `joint_positions` 超出 `upper_limits` / `lower_limits` → CLAMP 到限制值
2. `joint_velocities` 超出 `max_velocity` → CLAMP 縮放
3. `end_effector_pose` 超出 `bounds` → REJECT
4. 加速度估算（當前速度 - 上一速度 / dt）超出限制 → CLAMP

**測試**：

| 測試類型 | 內容 |
|---|---|
| Unit | 關節限制內 → PASS |
| Unit | 單關節超限 → CLAMP 到邊界值 |
| Unit | 末端超出工作空間 → REJECT |
| Unit | 速度超限 → CLAMP 且比例正確 |
| Safety regression | `tests/safety/test_motion_regression.py`：所有已知危險場景必須 REJECT 或 CLAMP |
| Property | Hypothesis：任意關節值，CLAMP 後必然在限制內 |

---

### 1J — Stackfile 解析器（基礎版）

**開發內容**：

| 模組 | 內容 |
|---|---|
| `dam/config/parser.py` | `StackfileLoader`：load(path), validate(path) |
| `dam/config/schema.py` | JSON Schema 或 Pydantic 模型定義 Stackfile 結構 |

Phase 1 只需支援：`guards`、`boundaries`、`tasks`、`safety` 區塊。`hardware`、`policy`、`simulation` 在後續 Phase 實現。

**測試**：

| 測試類型 | 內容 |
|---|---|
| Unit | 正確 YAML → 正確解析 |
| Unit | 缺少必要欄位 → 明確錯誤訊息 |
| Unit | 未知守衛名稱 → 拒絕載入 |
| Unit | boundaries 約束值型別驗證（max_speed 必須為數字） |
| Integration | 完整範例 Stackfile 解析 → GuardRuntime 正確初始化 |

---

### 1K — 測試工具集

**開發內容**：

| 模組 | 內容 |
|---|---|
| `dam/testing/mocks.py` | MockSourceAdapter, MockPolicyAdapter, MockSinkAdapter, MockSimulatorAdapter |
| `dam/testing/helpers.py` | inject_and_call(), assert_rejects(), assert_clamps(), assert_passes() |
| `dam/testing/pipeline.py` | run_pipeline(stack, obs_seq, actions) → List[CycleResult] |
| `dam/testing/safety.py` | safety_regression(scenarios) — 批次驗證已知危險場景 |

**測試**：

| 測試類型 | 內容 |
|---|---|
| Unit | Mock 適配器行為正確（回放序列、記錄結果） |
| Integration | run_pipeline 完整流程：mock source → guard → mock sink |

### Phase 1 退出標準

```python
# 這段測試必須通過，Phase 1 才算完成
def test_phase1_exit_criterion():
    """dam.step() 在純 Python mock 環境中跑完一個完整的 sense→validate→act 週期"""
    runtime = GuardRuntime.from_stackfile("examples/stackfiles/test.yaml")
    runtime.register_source("main", MockSourceAdapter([obs1, obs2, obs3]))
    runtime.register_policy(MockPolicyAdapter([action1, action2, action3]))
    sink = MockSinkAdapter()
    runtime.register_sink(sink)

    runtime.start_task("test_task")
    for _ in range(3):
        result = runtime.step()
        assert isinstance(result, CycleResult)
        assert result.risk_level is not None
    runtime.stop_task()

    # 至少有一些動作到達了 sink（不是全部被 REJECT）
    assert len(sink.received) > 0
```

---

## 2. Phase 2 — LeRobot 硬體整合

> 第一次接觸真實硬體。硬體目標：**SO-ARM101 follower arm**；策略目標：**LeRobot ACT**。

### 2.0 Phase 2 目標與邊界

**硬體**：SO-ARM101 follower arm（6 關節，Feetech STS3215 伺服，LeRobot USB 介面）
**策略**：LeRobot ACT（`lerobot/act` 類型，pretrained checkpoint）
**任務**：桌面 pick-and-place，單一物體
**參考 Stackfile**：`examples/so101_act_pick_place.yaml`

**Phase 2 新增依賴（硬性邊界）**：
- `lerobot` Python SDK（`pip install lerobot`）
- `maturin`（PyO3 建構工具，Rust → Python wheel）
- Rust toolchain（`rustup`）
- Phase 2 Docker 服務：`rust-builder`（含 Rust + maturin）

**Phase 2 不引入**：
- ROS2（Phase 3）
- 模擬器（Phase 4）
- Stage DAG 並行執行（Phase 3）

### 2.0 Phase 2 模組依賴圖

```
[Phase 1 完整系統] ────────────────────────────────┐
                                                     │
[2A] LeRobot Adapters (Source/Sink/Policy) ──────────┤
                                                     │
[2B] LeRobotRunner ──────────────────────────────────┤
                                                     │
[2C] Rust ObservationBus (MCAP ring buffer) ─────────┤
                                                     │
[2D] Rust MetricBus + RiskController ────────────────┤
                                                     │
[2E] Rust WatchdogTimer ─────────────────────────────┤
                                                     │
[2F] OOD Guard (L0) ─────────────────────────────────┤
                                                     │
[2G] Execution Guard (L3) ───────────────────────────┤
                                                     │
[2H] MCAP Context Capture ───────────────────────────┤
                                                     │
[2I] Stackfile hardware + policy 解析 ───────────────┘
                                                     │
                                                     ▼
                                   [EXIT] so101 arm pick-and-place
                                          ACT policy + L0+L2+L3 guards
                                          人為觸發違規 → fallback + E-Stop
```

### 2.0.1 Docker Compose 擴充（Phase 2）

```yaml
# 在 docker-compose.yml 新增：
services:
  rust-builder:
    image: rust:1.78-slim
    working_dir: /workspace
    volumes:
      - .:/workspace
      - cargo-cache:/root/.cargo
    command: >
      bash -c "cargo build --release &&
               maturin develop --release"

  test-hardware:
    # 同 test，但不排除 hardware 標記（需實體設備）
    build:
      context: .
      dockerfile: docker/Dockerfile.test
    volumes:
      - .:/workspace
      - /dev:/dev      # USB 裝置直通
    privileged: true
    command: pytest tests/ -v -m "hardware"

volumes:
  cargo-cache:
```

### 2A — LeRobot Adapters（so101 + ACT）

**目標介面**（LeRobot SDK）：
- `robot.get_observation()` → `{"observation.state": tensor, "observation.images.top": tensor, ...}`
- `robot.send_action(action_dict)` → 發送到伺服馬達
- `policy.select_action(obs_dict)` → `{"action": tensor[6]}`（ACT 回傳 chunk，取第一步）

| 模組 | 內容 |
|---|---|
| `dam/adapter/lerobot/source.py` | `LeRobotSourceAdapter`：`read() → Observation`；將 lerobot obs dict 轉換為 DAM `Observation` dataclass |
| `dam/adapter/lerobot/sink.py` | `LeRobotSinkAdapter`：`write(ValidatedAction)`；將 `target_joint_positions` 轉回 lerobot action dict |
| `dam/adapter/lerobot/policy.py` | `LeRobotPolicyAdapter`：`predict(Observation) → ActionProposal`；處理 ACT chunk（`n_action_steps`），每步取 index 0 |

**so101 關節映射**（`Observation.joint_positions` index）：
```
0: shoulder_pan
1: shoulder_lift
2: elbow_flex
3: wrist_flex
4: wrist_roll
5: gripper
```

**測試**：

| 類型 | 內容 |
|---|---|
| Unit | mock robot dict → `Observation` 欄位正確映射 |
| Unit | `ValidatedAction` → lerobot action dict 格式正確 |
| Unit | ACT chunk 截斷：輸出只有 index 0 |
| Integration `@pytest.mark.hardware` | 真實 so101 連線後 `source.read()` 不為 None |

### 2B — LeRobotRunner

| 模組 | 內容 |
|---|---|
| `dam/runner/lerobot.py` | `LeRobotRunner`：從 Stackfile `hardware` 區塊建構 robot + policy；封裝 `GuardRuntime.step()` 成自動迴圈 |

**主要行為**：
- `from_stackfile(path)` — 讀取 `hardware.preset`、`hardware.sources`、`policy.*`，建構 robot 和 policy 物件
- `run(task, n_cycles=None)` — 啟動迴圈，每 `1/control_frequency_hz` 秒呼叫一次 `step()`
- `step()` — 代理到內部 `GuardRuntime.step()`，傳回 `CycleResult`
- `stop()` — 優雅停止（先 `hold_position` 再斷線）

**測試**：Mock runner unit test；真實硬體 integration test 標記 `@pytest.mark.hardware`。

### 2C — Rust ObservationBus

| 模組 | 內容 |
|---|---|
| `dam-rs/crates/observation-bus/` | MCAP-backed 無鎖環形緩衝區 |
| `dam-rs/crates/observation-bus/src/pyo3.rs` | PyO3 綁定：`write(obs)`, `read_latest()`, `read_window(duration)` |

**Rust 測試**：
- `cargo test`：寫入/讀取正確性、併發讀寫安全性、環形覆蓋行為
- 基準測試（`cargo bench`）：讀寫延遲 < 100μs

**Python 側測試**：
- Unit：透過 PyO3 調用 write/read_latest，驗證資料一致性

### 2D — Rust MetricBus + RiskController

| 模組 | 內容 |
|---|---|
| `dam-rs/crates/metric-bus/` | 每個守衛的 SPSC 通道 |
| `dam-rs/crates/risk-controller/` | 窗口化風險聚合；atomic emergency flag |

**測試**：
- Rust：窗口化計算正確性、emergency flag 原子性
- Python：透過 PyO3 推送 metric → 查詢 risk_level 正確

### 2E — Rust WatchdogTimer

| 模組 | 內容 |
|---|---|
| `dam-rs/crates/watchdog/` | 週期截止時間強制；超時 → E-Stop |

**測試**：
- Rust：設定 10ms deadline，sleep 20ms → 驗證 E-Stop 觸發
- 安全測試：**Python GIL 被阻塞時，WatchdogTimer 仍然能觸發 E-Stop**

### 2F — OOD Guard（L0）

| 模組 | 內容 |
|---|---|
| `dam/guard/builtin/ood.py` | Autoencoder 重建誤差偵測，接收完整 obs |

**測試**：
- Unit：正常 obs → PASS；人造 OOD obs → REJECT
- Safety regression：已知 OOD 場景必須 REJECT

### 2G — Execution Guard（L3）

| 模組 | 內容 |
|---|---|
| `dam/guard/builtin/execution.py` | 邊界容器約束評估 + 節點超時 |

**測試**：
- Unit：約束內 → PASS；違反 → REJECT；超時 → REJECT
- Integration：與 BoundaryContainer 整合測試

### 2H — MCAP Context Capture

| 模組 | 內容 |
|---|---|
| `dam/logging/mcap_capture.py` | 違規時保存 ±30 秒的 ObservationBus 快照 |

**測試**：
- Unit：mock 違規 → 驗證 MCAP 文件生成且包含正確時間範圍

### 2I — Stackfile hardware + policy 解析

`StackfileLoader.load()` 已支援 Phase 2 所有區塊（`hardware`、`policy`、`loopback`、`risk_controller`）。
Phase 2 新增的工作：`LeRobotRunner.from_stackfile()` 讀取這些區塊建構真實物件。

| 解析目標 | 來源欄位 | 結果物件 |
|---|---|---|
| robot 實例 | `hardware.sources.follower_arm` | `lerobot.Koch` 或 `lerobot.ManipulatorRobot` |
| policy 實例 | `policy.pretrained_path`, `policy.device` | `lerobot.ACT` loaded from checkpoint |
| 關節限制 | `hardware.joints` 覆蓋 preset | 傳入 `MotionGuard` config_pool |
| workspace | `boundaries.*.nodes.*.constraints.bounds` | 傳入 `MotionGuard` config_pool |

### Phase 2 退出標準

```python
# 這段必須在真實硬體上通過（@pytest.mark.hardware）
def test_phase2_exit_criterion():
    """so101 + ACT pick-and-place with L0+L2+L3 guards, real hardware."""
    runner = LeRobotRunner.from_stackfile("examples/so101_act_pick_place.yaml")
    runner.start_task("pick_and_place")

    # 正常跑 10 個週期
    results = [runner.step() for _ in range(10)]
    assert all(isinstance(r, CycleResult) for r in results)
    assert all(r.risk_level in (RiskLevel.NORMAL, RiskLevel.ELEVATED) for r in results)

    # 人為觸發：手動推動機器臂超出 bounds
    # （實際測試在 MCAP 記錄分析中確認）

    runner.stop()

# 退出標準清單：
# ✓ L0（OOD）+ L2（Motion）+ L3（Execution）守衛全部啟用
# ✓ 關節位置正確映射到 Observation.joint_positions
# ✓ ValidatedAction 正確回送到 robot.send_action()
# ✓ 人為觸發違規 → REJECT → hold_position fallback 觸發
# ✓ 連續 2 次 REJECT → RiskLevel.ELEVATED → 最終 E-Stop
# ✓ /tmp/dam_loopback.mcap 生成，含 ±30s 違規前後片段
```

---

## 3. Phase 3 — ROS2 整合 + 進階 Runtime

> 相同守衛棧，不同硬體介面——只需更換 Stackfile

### 3.0 Phase 3 模組依賴圖

```
[Phase 2 完整系統] ────────────────────────────────┐
                                                     │
[3A] ROS2 Source/Sink Adapter ───────────────────────┤
[3B] ROS2Runner ─────────────────────────────────────┤
[3C] Rust ActionBus ─────────────────────────────────┤
[3D] Rust Hardware Guard (L4) ───────────────────────┤
[3E] Stage DAG 並行執行 ─────────────────────────────┤
[3F] process_group 跨進程隔離 ───────────────────────┤
[3G] Hot Reload（雙緩衝 + 柵欄）──────────────────────┤
[3H] 雙模式入口（run / step）────────────────────────┘
                                                     │
                                                     ▼
                                              [EXIT] ROS2 arm 使用相同守衛棧
                                                     只更換 Stackfile sources/sinks
```

### 開發內容

| 模組 | 關鍵要點 |
|---|---|
| **3A ROS2 Adapters** | 從 Stackfile 讀 topic 名稱自動建立 subscriber/publisher |
| **3B ROS2Runner** | 與 rclpy executor 整合，timer callback 模式 |
| **3C Rust ActionBus** | fallback 透過 PyO3 寫入；硬體從 Rust 端讀取 |
| **3D Hardware Guard (L4)** | 純 Rust 實現，監控電流/溫度，獨立於 Python 觸發 E-Stop |
| **3E Stage DAG** | `list[list[Guard]]` 啟動時構建；Stage 內守衛並行（ThreadPoolExecutor）；Stage 間循序 |
| **3F process_group** | 共享內存傳遞 obs，跨進程守衛隔離；只能引用 config pool 鍵 |
| **3G Hot Reload** | 文件監控 → 只解析變更區段 → 重建 _static_kwargs → 週期柵欄 → 雙緩衝原子交換 |
| **3H 雙模式** | `dam.run()`（託管循環）vs `dam.step()`（被動步進） |

### 測試要求

| 測試類型 | 內容 |
|---|---|
| Unit | ROS2 Adapter：mock rclpy 測試 subscribe/publish |
| Unit | Stage DAG：2 個守衛並行，驗證兩者都被調用 |
| Unit | Hot Reload：修改 Stackfile → _static_kwargs 正確更新 |
| Integration | 完整 Stage DAG + DecisionAggregator 管線測試 |
| Safety | L4 Hardware Guard：Rust 側 E-Stop 在 Python 線程被阻塞時仍能觸發 |
| Safety | Hot Reload 期間的週期不能丟失（柵欄不能阻塞超過 cycle_budget_ms） |

### Phase 3 退出標準

```
相同的守衛棧在 ROS2 機器臂上運行——不修改任何守衛代碼，
只更換 Stackfile 中的 sources/sinks 聲明。
```

---

## 4. Phase 4 — 模擬層

> 刻意排在最後：外部模擬器依賴重、啟動慢，且前三個 Phase 必須在沒有模擬的情況下正常工作。

### 開發內容

| 模組 | 關鍵要點 |
|---|---|
| **4A SimulatorAdapter ABC** | 介面：reset(), step(action), has_collision(), is_available() |
| **4B IsaacSimAdapter** | Isaac Sim 後端；從 Stackfile 載入場景 |
| **4C GazeboAdapter** | Gazebo 後端 |
| **4D SimPreflightGuard (L1)** | 多步前瞻：在影子線程上用 policy + simulator 預跑；截止前返回結果，超時 → TIMEOUT 不阻塞 |
| **4E Stackfile `simulation:` 區塊** | 將 SimulatorAdapter 注入 runtime pool |

### 測試要求

| 測試類型 | 內容 |
|---|---|
| Unit | L1 前瞻邏輯：mock simulator 有碰撞 → REJECT |
| Unit | L1 超時處理：simulator 太慢 → TIMEOUT 但不阻塞主循環 |
| Unit | SimulatorAdapter.is_available() == False → L1 優雅跳過 |
| Integration | 完整 Stage DAG 含 L1 並行 L2：L1 REJECT 優先於 L2 PASS |

### Phase 4 退出標準

```
L1 守衛在 Isaac Sim 中拒絕了一個會造成碰撞的動作，
且該拒絕發生在動作送到真實硬體之前。
```

---

## 5. Phase 5 — 可觀測性 & UI

> 不要在 Phase 2 穩定於真實硬體前動手。可觀測性只有在有東西可觀測時才有用。

### 開發內容

| 模組 | 關鍵要點 |
|---|---|
| **5A WebSocket Telemetry** | 即時事件推送（obs、guard results、risk level） |
| **5B REST API** | Boundary CRUD、Runtime Control（start/stop/e-stop）、Risk Log 查詢 |
| **5C Web Dashboard** | 觀測值檢視器、守衛狀態、風險等級、節點切換可視化 |
| **5D 分佈式追蹤檢視器** | trace_id 鏈接的守衛結果時間線 |

### 測試要求

| 測試類型 | 內容 |
|---|---|
| Unit | WebSocket 訊息格式正確 |
| Unit | REST API 端點回應正確（使用 TestClient） |
| Integration | 完整 pipeline + telemetry 整合：step → 守衛結果即時推送 |
| E2E | Dashboard 在瀏覽器中正確顯示即時資料 |

### Phase 5 退出標準

```
操作員透過 Web Dashboard 即時觀察：
- 每個守衛每週期的決策結果
- 風險等級變化
- 節點切換歷程
- 點擊 trace_id 跳轉到對應的完整守衛管線結果
```

---

## 6. 目錄結構參考

```
dam/
├── dam/                          # Python Control Plane
│   ├── __init__.py               # 公開 API：dam.step, dam.run, dam.Runner
│   ├── types/                    # [1A] 核心資料型別
│   │   ├── observation.py
│   │   ├── action.py
│   │   ├── result.py
│   │   └── risk.py
│   ├── guard/                    # [1D, 1I, 2F, 2G] 守衛系統
│   │   ├── base.py               # Guard ABC, GuardLayer IntEnum
│   │   ├── aggregator.py         # DecisionAggregator
│   │   ├── layer.py              # GuardLayer(IntEnum)
│   │   └── builtin/
│   │       ├── motion.py      # [1I] L2
│   │       ├── ood.py            # [2F] L0
│   │       ├── execution.py      # [2G] L3
│   │       └── sim_preflight.py  # [4D] L1
│   ├── boundary/                 # [1B, 1E] 邊界系統
│   │   ├── node.py
│   │   ├── constraint.py
│   │   ├── container.py          # ABC
│   │   ├── single.py
│   │   ├── list_container.py
│   │   └── graph.py
│   ├── registry/                 # [1C, 1F] 註冊中心
│   │   ├── callback.py
│   │   └── fallback.py
│   ├── fallback/                 # [1F] 回退系統
│   │   ├── registry.py
│   │   ├── builtin.py
│   │   └── chain.py
│   ├── injection/                # [1G] 注入池
│   │   ├── pool.py
│   │   ├── resolver.py
│   │   └── static.py
│   ├── runtime/                  # [1H] 守衛運行時
│   │   └── guard_runtime.py
│   ├── adapter/                  # [2A, 3A] 適配器
│   │   ├── lerobot/
│   │   └── ros2/
│   ├── runner/                   # [2B, 3B] Runner
│   │   ├── lerobot.py
│   │   └── ros2.py
│   ├── config/                   # [1J] 配置
│   │   ├── parser.py
│   │   └── schema.py
│   ├── logging/                  # [2H] 日誌
│   │   └── mcap_capture.py
│   ├── decorators.py             # @dam.guard, @dam.callback, @dam.fallback
│   ├── testing/                  # [1K] 測試工具
│   │   ├── mocks.py
│   │   ├── helpers.py
│   │   ├── pipeline.py
│   │   └── safety.py
│   └── cli.py                    # dam run / dam validate / dam replay
│
├── dam-rs/                       # Rust Data Plane
│   ├── crates/
│   │   ├── observation-bus/      # [2C]
│   │   ├── action-bus/           # [3C]
│   │   ├── metric-bus/           # [2D]
│   │   ├── risk-controller/      # [2D]
│   │   ├── watchdog/             # [2E]
│   │   └── hardware-guard/       # [3D] L4
│   └── Cargo.toml
│
├── tests/
│   ├── unit/                     # 每次 commit 執行
│   ├── integration/              # 每次 commit 執行
│   ├── safety/                   # 每次 commit 執行（阻擋合併）
│   ├── property/                 # 每日 nightly
│   └── hardware/                 # 手動觸發（@pytest.mark.hardware）
│
├── scripts/
│   └── tests.sh                  # 一鍵跑全部測試（整合測試使用非預設端口）
│
├── .github/workflows/ci.yml
├── pyproject.toml
└── README.md
```

---

## 7. 里程碑時間線（建議）

| Phase | 估計工期 | 前置條件 | 交付物 |
|---|---|---|---|
| **Phase 1** | 4–6 週 | 無 | 純 Python 安全引擎 + MotionGuard + 測試套件 |
| **Phase 2** | 4–6 週 | Phase 1 退出標準通過 | LeRobot 硬體驗證 + Rust bus + L0/L3 + MCAP |
| **Phase 3** | 3–4 週 | Phase 2 退出標準通過 | ROS2 支援 + Stage DAG + Hot Reload + L4 |
| **Phase 4** | 2–3 週 | Phase 3 退出標準通過 | L1 SimPreflight + Isaac Sim/Gazebo |
| **Phase 5** | 3–4 週 | Phase 2 穩定 | Dashboard + API + 追蹤器 |

總計約 16–23 週（4–6 個月），具體取決於團隊規模與硬體到位時間。Phase 5 可與 Phase 3/4 並行開發。
