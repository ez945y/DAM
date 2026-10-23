---
name: dam-agent
description: "Configure, integrate, diagnose, and extend DAM (Detachable Action Monitor / robot-dam): Stackfiles, Guardrail Python APIs, boundary callbacks, and guard runtime behavior. Use for DAM-specific robot action validation and incident triage; not for unrelated robotics or general coding tasks."
---

# DAM Agent

依據目前 checkout 的實作，完成 DAM 設定、整合或診斷。DAM 位於 policy 與 actuator 之間，評估 proposed action，再輸出 validated command 或 fallback；它是研究用防護框架，不能把通過 guard 描述為硬體安全認證。

## 先定位版本與任務

- 專案根目錄應含 `pyproject.toml`（package name 為 `robot-dam`）、`dam/` 與 `examples/stackfiles/`。從工作目錄或使用者指定位置定位，不硬編碼作者的本機路徑。若此 skill 透過 symlink 安裝，可解析 skill 的實體位置，再檢查上兩層是否為 DAM checkout。
- 先讀根目錄適用的 agent 指示（如 `AGENTS.md`、`CLAUDE.md`）；進行維護時再讀 `session-handoff.md` 與相關進度。這些歷史記錄可能落後於程式。
- 讀 `pyproject.toml`、涉及的公開 API、schema 與測試。API 事實以目前原始碼和測試為準；`docs/DAM_LLM_System_Prompt.md` 是本技能的入口，不另外維護一套規格。
- 區分需求：設定 Stackfile、嵌入既有控制迴圈、開發 DAM 核心，或排查事故。先確認 action 語意、觀測群組、任務名稱及執行環境；可從現有設定推得的資訊不重問。
- 以下命令皆從 DAM 根目錄執行。優先使用既有 `.venv/bin/python`；CLI 可使用 `make dam ARGS="..."` 或 `.venv/bin/python -m dam.cli`。其他環境先確認 interpreter 與 package 來源，避免混用已安裝版本與 checkout。
- 如果 `.venv/bin/dam` 的啟動路徑已失效，但目前 `.venv/bin/python` 可使用，改用 `.venv/bin/python -m dam.cli` 執行相同子命令；先回報環境問題，不為了文件驗證重建整個環境。

## 選擇最小整合面

| 需求 | 使用入口 |
| --- | --- |
| 已有 policy / recording / simulator loop，只需要驗證命令 | 重用 `dam.Guardrail` instance |
| 單筆離線驗證 | `dam.guardrail` |
| LeRobot processor pipeline | `dam.GuardrailProcessorStep`；確認實際依賴與 observation 傳遞 |
| 由 DAM 管理感測、policy、sink 與生命週期 | `dam.run` 或 `dam.build_runner` |
| 內建規則可表達的限制 | 修改 Stackfile callback 參數 |
| 新的領域規則 | 註冊 callback；僅在生命週期或介面需求超出 callback 時新增 guard / adapter |

不要為整合 DAM 改寫 policy 權重或繞過硬體驅動。只在使用者要求核心變更時修改 DAM 的實作。

## 編輯 Stackfile

1. 從 `examples/stackfiles/` 選擇符合機器人與用途的範例。`minimal.yaml` 僅示範最小 schema，沒有完整硬體識別；不可視為任意機器人的可直接執行設定。
2. 用 `dam/config/schema.py` 查欄位，用 `.venv/bin/dam callbacks --json` 查 callback 名稱及參數。現行 `guards` 是清單（例如 `- L1: motion`），boundary node 用 `callback` 和 `params`，不是舊的 `guards.builtin` 或 `constraint` 形狀。
3. 明確設定 `tasks.<task>.boundaries`，並檢查 `safety.always_active`。存在於 `boundaries` 的規則不等於已在目標 task 啟用。不要只檢查 YAML 是否合法。
4. 對齊 joint 名稱、向量順序、維度、座標系、時間基準與單位。內部 joint 量通常是 radians；外部 degree conversion 由 motor interface 的 `source.degrees_mode` 或 `hardware.degrees_mode` 決定，不由 preset 決定。不能憑機器人名字推定。
5. 非 joint 命令以 `hardware.action_layout` 的 segment `keys` 宣告語意，例如 `[v, omega]`。確認 solver 已註冊及其 capabilities；不可把速度、EE pose 直接套進 joint-position guard，也不可假設 schema 接受就代表 IK/FK 已實作。
6. 頻率欄位為 `safety.control_hz`、`safety.telemetry_hz`、`safety.slow_lane.task_hz`。確認所需 enforcement mode；`monitor` 記錄違規並不等同 enforce 限制。
7. 使用明確檔案路徑執行 `dam validate <path>` 與 `dam inspect <path>`，再做與任務相關的離線行為驗證。兩個 CLI 檢查無法證明 callback 已註冊、硬體接線正確或防護完整。

## Guardrail 呼叫契約

- 現行呼叫是 `rail({"joints": obs, "action": proposed})`，不是 `SafetyGuard(action, obs)`。`action` 是待驗證命令，其餘 key 為觀測群組；mobile base 可使用 `base_pose` 等群組，不必偽造 joints。
- 建立 instance 時明確指定目標 task；提供可解析的 joint names / preset、action layout 與單位。閱讀 `rail.describe()` 契約；callback 依參數名稱取得觀測群組，不能與保留 runtime keys 衝突。
- 缺失觀測、錯誤維度、NaN/Inf、缺少 action dictionary keys 應修正輸入來源，不能補零或捕捉錯誤後把原始 action 直接送出。
- 控制迴圈只送出 Guardrail 回傳值，並讀 `rail.last_results` 的 decision、guard name 與 reason。回傳型別會對應 action 輸入；修正值可能不同，不能忽略它。
- 明確選擇符合 action 語意的 `safe_action`：位置控制 arm 可保持目前 joints；速度 base 可使用零速度。不要把全零向量假定為任意 EE pose / torque controller 的安全停止方式。非 joint command-space 的預設 hold 會拒絕建構；必須明確提供 fallback 向量或 `"zero"`，不能推定零向量適合所有控制器。
- 需要詳細結果時使用 `rail.evaluate(inputs)`；`preflight` 做不啟動資源的靜態檢查，`request_reload`／`reload_receipt` 區分 pending 與 applied，`reset`／`diagnostics` 支援恢復與診斷。細節與限制讀 `docs/library-api.md`；可執行案例見 `examples/sdk_offline.py`。
- 多次呼叫重用 instance 以保留歷史；使用 context manager 或 `close()` 釋放背景 worker / recording。不要每個 cycle 重建 one-shot guard。
- Slow lane 的 ready 只代表有 verdict，可能含 REJECT，也可能對應較舊 snapshot。涉及等待、時效或 stale 行為時讀 `docs/library-api.md` 與 `dam/runtime/guard_runtime.py`，確認 `wait_for_slow`、首次 verdict、timeout 與 enforcement mode 的交互作用。

## 擴充與核心維護

- 自訂規則以 `@dam.callback("name", layer="L1")` 等正確層級註冊，並在建立 Guardrail/runtime 前 import 註冊模組；callback 所屬層要與 boundary / guard 一致。
- L0 處理 OOD，L1 處理運動限制，L2 處理任務執行，L3 處理硬體健康。這是責任劃分，不代表固定執行拓撲。順序、並行與 slow lane 必須查看目前 stage builder / execution engine，不能沿用 README 的概括敘述。
- 保留 PASS / CLAMP / REJECT / FAULT、fallback、timeout 與 enforcement 語意；失敗不得默默變成原始 action 的放行。修改聚合或 QP 時，檢查修正後命令同時滿足所有啟用約束。
- 與裝置無關的規則留在 guard / callback 層；裝置 IO 留在 adapter / interface。不要在控制熱路徑加入檔案 IO、模型載入或重複 signature inspection。
- 依變更選擇現有測試；guard 修改需涵蓋相關 pass、clamp、reject/fault 與 regression 情境。純文件修改做結構與範例驗證，無需新增只比對文字的測試。
- 遵循專案的公開 API 改名政策，完整更新呼叫端；不要自行加入相容 alias。只在使用者可見介面變更時同步相關文件。

## 實機與事故邊界

實機不動、抖動、反覆 clamp/reject 或 UI 與硬體不一致時，先讀 [references/project-map.md](references/project-map.md) 的事故流程。第一輪只取證，不以 run、reconnect 或 calibration 嘗試重現。

設定或診斷請求不自動授權致動。需要實機執行時，核對既有授權是否涵蓋該裝置、任務和操作；缺少必要授權才詢問。先完成可離線審查的設定、驗證與執行方案。不要為了讓機器動起來放寬 guard、停用防護或切成 monitor。

## 驗證與交付

按需讀 [references/project-map.md](references/project-map.md)：它提供原始碼路由、可離線執行的 Guardrail 範例與事故判讀。

交付時說明修改內容、涉及的 task / action contract、實際執行的驗證，以及尚未驗證的 runtime / 硬體部分。提供可直接開啟的檔案與必要命令，不把 schema 通過、離線驗證或模擬結果說成已完成實機驗證。
