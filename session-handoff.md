# session-handoff.md — 會話交接摘要

> 最後更新: 2026-05-25 19:37

## 當前已驗證

- Physical-robot incident triage now has a safe first command: `.venv/bin/python scripts/mcap_triage.py --json`.
- `mcap_triage.py` is read-only, supports combined msgpack and split-topic JSON MCAP layouts, and never starts a task or sends actions.
- `joint_diagnostics.py` no longer moves hardware by default; a new motion-producing session requires explicit `--run`.
- Latest read-only smoke inspection of `session_a28b7324_1779707257.mcap` found 848/848 clamped cycles but observable motion on all six joints; it did not repeat the earlier non-responsive-joint finding.
- RQ1 runner now uses DAM `OODContext` and public OOD backends rather than mutating private `OODGuard` flow state.
- RQ1 output reports active signals and explicitly reports `action` as not scored.
- Vision RQ1 only evaluates frames that actually have attached images; missing-image zero padding is no longer mixed into its evaluation set.
- Dataset/model cache hit behavior is verified with a repeated smoke run and keyed by feature configuration.
- Frontend RQ1 result card displays signal provenance and refreshes immediately after a completed run.
- L0 surface is unified as `ood_detector` with a selectable backend; new UI-created OOD boundaries default to Real-NVP and old algorithm callback names are no longer registered.
- Unified `ood_detector` now forwards vision, NLL sigma, Welford threshold and device parameters, and loads a Real-NVP runtime bundle without requiring an unrelated memory-bank path.
- Unit tests: 580 passed; frontend tests/build and docs check passed.

## 未驗證 / 待確認

- **Persistent runtime clamps**: latest physical session still records `joint_velocity_limit` and `task_gripper_sequence` clamps on every cycle; diagnose policy/start state/guard configuration before relaxing safety.
- **Gripper unit semantics**: the ACT output versus `task_gripper_sequence` threshold unit contract needs a focused correction and physical validation.
- **RQ1 action feature 尚未實作**: HF dataset contains action, but current Real-NVP input remains `observation.state` plus optional vision. The UI now says so explicitly.
- **State-only cannot identify recover-failure reliably**: full-dataset one-epoch UI smoke run produced abnormal detection=3.7%; this is evidence of an inadequate feature mode, not a final metric.
- **Vision threshold calibration**: image-fused separation has been observed, but threshold strategy still needs held-out validation calibration before it can support conclusions.
- **機器人微振盪**: 模型輸出品質問題，不是 guard pipeline 問題。

## 本輪改動摘要

| Commit | 改動 |
|--------|------|
| current delivery | Read-only MCAP triage CLI, safe joint diagnostic default, agent/docs guidance |
| current delivery | RQ1 public backend refactor, deterministic cache identity, honest UI feature provenance, vision frame filtering |
| current delivery | Unified L0 OOD boundary and RQ1 export with Real-NVP default |

## 下一步最佳動作

1. **實作 feature mode**: 將 dataset `action` 與 per-episode temporal residual 納入一個明確的 RQ1 mode，與 state-only / vision-fused 並列比較。
2. **重新校準 threshold**: 用 normal validation split 設定 operating threshold，避免拿 test result 回推門檻。
3. **重跑正式 RQ1**: 完成上述兩步後才產生論文可使用的 detection/FPR 結果。

## 命令速查

```bash
.venv/bin/python scripts/mcap_triage.py --json       # 實機 incident 第一個唯讀診斷
.venv/bin/python scripts/mcap_triage.py --compare data/robot/sessions/session_known_good.mcap --json
make dev                                           # 開發模式
make test                                          # 完整測試
cd dam-console && npm run build                    # 重建前端
python scripts/run_l0_calibration.py               # 跑 L0 calibration (HF data)
python scripts/run_l0_calibration.py --hf-repo MikeChenYZ/soarm-fmb-v2  # 指定 HF repo
python -m pytest tests/unit/ -x                    # 快速 unit test
```
