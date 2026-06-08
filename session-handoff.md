# session-handoff.md — 會話交接摘要

> 最後更新: 2026-06-08 (Isaac resolver-backed EE wrapper)

## 本輪完成

### 1. `input_space` schema contract

`hardware.input_space` 與 `policy.input_space` 已正式納入 Stackfile schema。

```yaml
hardware:
  preset: so101_follower
  input_space: joint          # "joint" | "ee"; default "joint"

policy:
  type: act
  input_space: joint          # must match hardware.input_space when policy exists
```

已落地行為：
- `HardwareConfig.input_space` default = `"joint"`
- `PolicyConfig.input_space` default = `"joint"`
- 只允許 `"joint"` / `"ee"`，大小寫會 normalize
- `hardware.input_space != policy.input_space` 時 schema validation 直接 fail
- 沒有 `policy:` 的 SafetyGuard-only stackfile 不做一致性檢查

### 2. SafetyGuard input-space guardrail and resolver-backed EE path

`dam.SafetyGuard(..., input_space=...)` 已支援讀取 / override action-space declaration。

已落地行為：
- 預設讀 `hardware.input_space`
- API override 會覆蓋 stackfile 設定
- `"joint"` path 維持既有行為
- `"ee"` path 需要 `kinematics_resolver`
- 有 resolver 時：EE pose → IK joint proposal → existing guard pipeline → FK safe EE pose
- 無 resolver 時：明確 `ValueError`

Resolver protocol：

```python
class Resolver:
    def inverse_kinematics(self, target_ee_pose, current_joint_positions):
        ...

    def forward_kinematics(self, joint_positions):
        ...
```

這仍是 API-level support，不是 runtime/sink-level EE dispatch。內部 guard pipeline 保持 joint target contract，避免新增第二套 `ValidatedEEAction`。

### 3. EE pose observation injection retained, but not action conversion

`SafetyGuard.set_ee_pose()` 保留，作用是把目前 EE pose 放進 `Observation.end_effector_pose`，供 workspace / EE guard 使用。

注意：`Observation.end_effector_pose` 是 current observation，不是 target EE action。下一步若做真正 EE policy，目標應走 `ActionProposal.target_ee_pose`，不要混用 observation 欄位。

### 4. Baseline test fixes

修了兩個 `make test` 會踩到的測試環境問題：
- `tests/unit/test_dataset_hardware_replay.py`：unit test mock `CameraFrameHub`，避免 routing test 依賴本機 Rust `dam_rs.ImageHub`
- `tests/unit/test_vision_feature_extractor.py`：真模型測試 gated on torch/torchvision；在 repo `.venv` 有 torch 時實際跑過，在缺 optional deps 的系統 Python 不會假失敗
- `SafetyGuard.__del__`：初始化早期失敗時不再丟 unraisable warning

### 5. Docs updated

`docs/quick-stack.md` 已更新 `hardware.input_space` / `policy.input_space` 欄位與目前 EE 限制。

### 6. Isaac Lab sidecar cleanup (`/tmp/isaac_lab_study`)

已把 Isaac demo 收斂成真實可交付的 joint target filter：
- `scripts/dam_safety_demo.py`
- `scripts/dam_teleoperate_demo.py`
- `tools/controll_scripts/safety/dam_wrapper.py`
- `tools/controll_scripts/safety/__init__.py`
- `tools/controll_scripts/safety/soarm_isaac_safety.yaml`

設計取捨：
- 不新增 EE-policy demo
- 不加 `input_space` future key
- 不在 Isaac wrapper 裡調 `set_ee_pose()`
- 不保留 `--controller osc` 假支援
- wrapper 會檢查匯入的 `dam` 是否真的提供 `SafetyGuard`

### 7. Isaac concrete resolver adapter (`/tmp/isaac_lab_study`)

已新增 sidecar concrete adapter：
- `tools/controll_scripts/safety/isaac_resolver.py`
- `DAMSafetyWrapper.attach_isaac_controller(...)`
- `DAMSafetyWrapper.filter_ee(...)`

落地行為：
- EE target pose 使用 DAM convention `[x,y,z,qx,qy,qz,qw]` 進入 `SafetyGuard(input_space="ee")`
- wrapper 對外仍接受 Isaac pose `[x,y,z,qw,qx,qy,qz]`
- resolver 用 live Isaac articulation + existing IK controller 做 target EE pose → joint proposal
- resolver 用 bundled SO-ARM-101 URDF + Pinocchio 做 validated joint target → safe EE pose FK
- `filter_ee()` 回傳 Isaac 要套用的 safe arm joint targets
- `last_safe_gripper` 保存 DAM validated gripper target；demo 也改用它，不再套 raw gripper command
- `scripts/dam_safety_demo.py` / `scripts/dam_teleoperate_demo.py` 已移除重複 IK 解算，改走 wrapper

## 驗證證據

主 DAM repo：
- `python -m pytest tests/unit/ -x -q` — 689 passed, 40 skipped（system Python）
- `make lint` — passed
- `make docs-check` — passed
- `make test` — passed（第二次 full run after resolver path）
  - pre-commit passed
  - unit: 732 passed
  - integration: 28 passed
  - safety: 35 passed
  - property: 2 passed
  - Rust: passed
  - Jest: 109 passed

Isaac sidecar:
- `python -m py_compile scripts/dam_safety_demo.py scripts/dam_teleoperate_demo.py tools/controll_scripts/safety/dam_wrapper.py tools/controll_scripts/safety/__init__.py` — passed
- `python -m py_compile scripts/dam_safety_demo.py scripts/dam_teleoperate_demo.py tools/controll_scripts/safety/dam_wrapper.py tools/controll_scripts/safety/isaac_resolver.py tools/controll_scripts/safety/__init__.py tests/test_dam_safety_wrapper.py` — passed
- `PYTHONPATH=tools /Users/chenyizhong/Documents/Claude/Projects/Security\ Guard.nosync/.venv/bin/python -m pytest tests/test_dam_safety_wrapper.py -q` — 3 passed
- Full Isaac launch not run; shell environment lacks `isaaclab`

## `/review` 結論

### Review 1 — avoid overdesign

不要新增 `EEActionProposal` / `ValidatedEEAction` / 第二套 pipeline。既有合約已經有：
- `ActionProposal.target_ee_pose`
- `Observation.end_effector_pose`
- runtime pool `ee_pos`, `ee_rot`, `J_linear`, `J_angular`

真正 EE support 應薄薄落在 API / policy adapter / resolver boundary，safety chassis 仍維持 joint output 主線。

### Review 2 — avoid design transfer

不要在 Isaac repo 先寫未落地的 EE demo。這會把 DAM 主 repo 還沒完成的 API 偽裝成 integration example。

### Review 3 — implementation shape used

真正 EE path 不讓 SafetyGuard 猜 IK，而是要求 caller 注入 resolver：
- FK: joint positions → EE pose / Jacobian
- IK: current joints + target EE pose → joint proposal
- configured resolver missing 時保持明確 error

已落地 EE path：
1. parse EE action into `ActionProposal.target_ee_pose`
2. resolver IK produces `target_joint_positions`
3. existing guard pipeline validates joint action
4. resolver FK maps validated joint positions back to EE pose

## 下一步建議

1. Commit Isaac concrete resolver adapter sidecar unit.
2. 下一個交付單元：在 IsaacLab runtime 中 launch `scripts/dam_safety_demo.py --controller ik`，確認 Pinocchio FK frame 與 Isaac body frame 對齊。
3. 再下一個交付單元：極小 EE-policy snippet 使用 resolver path。
4. 最後再考慮 runtime pool 是否需要 `target_ee_pos`，不要提前加。
