# DAM 查證入口與離線驗證

下列路徑皆相對於目前 DAM checkout 根目錄。只讀本次任務所需部分；檔案位置或介面不同時，用 `rg` 定位目前實作，勿照搬歷史記錄。

## 原始碼路由

| 問題 | 優先讀取 |
| --- | --- |
| 公開 API、輸入轉換、fallback、cleanup | `dam/__init__.py`、`dam/api.py`、`tests/unit/test_api.py`、`docs/library-api.md` |
| YAML schema、預設值與 preset 展開 | `dam/config/schema.py`、`dam/config/loader.py`、`assets/presets.yaml` |
| callback 註冊、參數注入、內建規則 | `dam/decorators.py`、`dam/boundary/callbacks/`、`dam/guard/pipeline.py`、`docs/boundary-callbacks.md` |
| task 啟用、stage 排序、slow lane | `dam/runtime/guard_runtime.py`、`dam/runtime/execution_engine.py`、`dam/runtime/slow_lane.py` |
| 多規則合併、QP 修正與決策 | `dam/runtime/merge_policy.py`、`dam/runtime/qp_solver.py`、`dam/types/result.py` |
| 感測、policy、命令出口與 runner | `dam/runtime/factory.py`、`dam/adapter/`、`dam/interface/`、`dam/runner/` |
| Rust bus、watchdog、跨語言行為 | `dam-rust/` 及其 Python 呼叫端 |
| Console 顯示與 API 狀態 | `dam/services/`、`dam-console/`、`docs/services-api.md` |
| 記錄、replay、事故證據 | `scripts/mcap_triage.py`、`scripts/joint_diagnostics.py`、`docs/loopback-logging.md` |
| 最小 joint 設定 | `examples/stackfiles/minimal.yaml` |
| mobile base 自訂 callback / solver | `examples/mobile_base_guardrail.py`、`examples/stackfiles/jetbot_lane_safety.yaml` |
| 錄製與 LeRobot 整合 | `examples/safe_record.py`、`docs/getting-started/safe-recording.md` |

## 離線 Guardrail smoke example

這個範例使用六個虛擬 joint 名稱與已知 radians 輸入，僅驗證內建 joint-position boundary；不連接硬體，也不代表六軸實機的安全設定。由 DAM 根目錄以現有 Python 環境執行：

```python
import numpy as np
import dam

with dam.Guardrail(
    "examples/stackfiles/minimal.yaml",
    task="default",
    joint_names=[f"joint_{i}" for i in range(6)],
    degrees_mode=False,
    safe_action="hold",
    quiet=True,
) as rail:
    observed = np.zeros(6)
    unchanged = rail({"joints": observed, "action": np.zeros(6)})
    np.testing.assert_allclose(unchanged, np.zeros(6))

    corrected = rail({"joints": observed, "action": np.full(6, 10.0)})
    upper = np.array([1.8243, 1.6, 1.5, 1.5, 3.0741, 1.7453])
    lower = np.array([-1.8243, -1.6, -1.6, -1.5, -3.0741, 0.0])
    assert np.all(np.isfinite(corrected))
    assert np.all(corrected <= upper + 1e-9)
    assert np.all(corrected >= lower - 1e-9)
    assert any(r.decision == dam.GuardDecision.CLAMP for r in rail.last_results)
    for result in rail.last_results:
        print(result.decision.name, result.guard_name, result.reason)
```

編輯 Stackfile 後還需執行 `.venv/bin/dam validate <path>` 與 `.venv/bin/dam inspect <path>`。修改此範例使用的 YAML 時，同步確認示範的維度與上下限仍一致。

## 事故取證

先檢查目前 checkout 的 `CLAUDE.md` Incident Protocol。現有唯讀入口：

```bash
.venv/bin/python scripts/mcap_triage.py --json
.venv/bin/python scripts/mcap_triage.py --no-api --json
```

第一個入口可取得 API 狀態；離線或服務不可用時使用 `--no-api`。調查用的 MCAP 必須與事故時段對應，不要把任意最新 session 當成事故證據。`joint_diagnostics.py` 無參數是唯讀，`--run` 是致動，需使用者明確要求。

| Finding | 正確判讀與後續 |
| --- | --- |
| `runtime_not_running` | loop 已停止；無法由此推論先前執行中的故障，不自動重新啟動 |
| `all_cycles_clamped` | action 被規則修正；檢查 reasons 與 active boundaries，不直接判定馬達失效 |
| `command_without_response` | 有 validated command 但觀測未跟隨；核對 calibration、起始姿態與硬體證據，不直接放寬限制 |
| `rejected_without_validated_command` | action 在發送前遭拒絕；追查 guard / fallback 原因，不描述為致動失敗 |
| `hardware_guard_event` | 硬體健康告警；維持唯讀調查並呈報具體事件 |

只有確認 robot、task 與 calibration 一致，才使用 `--compare <known-good.mcap>` 作 baseline 比較。報告區分原始提案、validated command、實測 observation 與 UI 顯示；保留時間與單位，指出哪些結論有資料支持。
