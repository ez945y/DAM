# session-handoff.md — 會話交接摘要

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
