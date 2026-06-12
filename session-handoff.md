# session-handoff.md — 會話交接摘要

> 最後更新: 2026-06-12 (Teleop inertia root-cause fix — acceleration limiter in command domain)

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
