# session-handoff.md — 會話交接摘要

> 最後更新: 2026-05-30 (make record 實戰修復)

## 本輪工作

### JointLayout Contract — 多體機器人 joint 分組合約

- `JointChain` + `JointLayout`，gripper 是 chain 屬性
- 整合到 `RobotPreset`，用 joint names 定義（不數 index）
- Typo 偵測、重複偵測、rich repr、zero-config auto-derive
- `pool["joint_layout"]` 流入 runtime，callbacks 可讀
- 30 tests，commit `e7b9625` → `d509292`

### make record 實戰修復

**Bug 1: Guard 從未生效**（commit `dc0b8b8`）
- `unittest.mock.patch.object` 改了 source module 但 lerobot 用 `from ... import` 拿到 local binding
- Fix: patch 在 consumer module（`lerobot.scripts.lerobot_record`）上

**Bug 2: 加速度限制偷偷啟用**（commit `08ee61a`）
- `max_acceleration` 預設 10 rad/s²，teleop 每個 cycle 都被 acceleration clamp
- Fix: 預設改為 disabled，opt-in via stackfile

**Bug 3: dt 不匹配**（commit `7f04a46`）
- Guard 用固定 `dt = 1/30` 但實際 loop 跑 ~18Hz → velocity limit 比預期緊 40%
- Fix: `SafetyGuard.__call__` 量實際 dt，覆蓋 pool["dt"]

**Bug 4: MCAP loopback 在 validate() 模式無效**（commit `769291a`）
- `validate()` 不觸發 telemetry，stackfile 寫了 loopback: 也沒用
- Fix: `validate()` 如果 loopback 在錄就 submit cycle

### Recording DX

- **Edge-triggered feedback**：clamp 開始印一行，結束印 duration+count，不 flood
- **Session summary**：結束時印 cycle 總數、clamp/reject 統計、top boundaries
- **Guard log**：砍掉自建 `_GuardLogWriter`，改用現有 MCAP loopback

### 未解：J2 (elbow_flex) 觀察值正負反轉

**現象**：
- Debug 顯示 `J2 obs=+89.9°` 但物理位置實際是 -89.9°
- Guard 基於錯誤的正值做 clamp，送出的命令方向反了
- Motor 收到反向命令後不動（被 position limit 擋住）
- Leader 從 +99° 到 -94° 大幅移動，follower 完全不追

**排查方向**：
- Follower calibration `elbow_flex` 的 `drive_mode`（兩個 calibration 檔都是 0）
- Leader vs follower 的 `homing_offset` 差異：leader -1827，follower -1955
- 不是 DAM 問題——lerobot 送進來的 obs 就已經反了
- 驗證：不用 DAM 直接跑 `lerobot-record` 看 J2 是否正常

**兩個 calibration 檔差異**（follower）：
- `follower_arm.json`: wrist_roll offset=1474, range [0, 4095]
- `my_awesome_follower_arm.json`: wrist_roll offset=-1598, range [7, 4014]
- stackfile 用 id=`follower_arm`，匹配 `follower_arm.json`

## 當前已驗證

- `python -m pytest tests/unit/ tests/safety/ -x -q` — 697 passed, 31 skipped
- `ruff check` + `mypy` — all passed
- `dam validate examples/stackfiles/*.yaml` — 6/6 valid
- 實機 `make record`：guard injection 確認生效，live feedback 正常

## Commits（本輪）

- `e7b9625` feat: add JointLayout contract
- `df3d323` chore: export JointLayout from dam.types
- `e985d4a` refactor: chain-based model with gripper as attribute
- `d509292` refactor: integrate into preset — name-based chains, typo detection
- `cd50da3` feat: live guard feedback, session summary
- `a188cd7` fix: edge-triggered feedback + flush + timing
- `cb69e42` refactor: remove _GuardLogWriter — use MCAP loopback
- `4807911` fix: remove inactive loopback from safety.yaml
- `769291a` feat: validate() submits to MCAP loopback when configured
- `dc0b8b8` fix: patch make_default_processors on consumer module
- `7f04a46` fix: use actual dt in SafetyGuard
- `08ee61a` fix: disable acceleration limit by default

## 命令速查

```bash
make record                             # 安全錄製
make record ARGS="--stackfile=my.yaml"  # 自訂 stackfile
python -m pytest tests/unit/ -x -q      # 快速 unit test
dam validate examples/stackfiles/*.yaml # 驗證 stackfile
dam callbacks                           # 列出內建 callbacks
```
