# L6／R10 可執行範例與交付指南

日期：2026-10-22。狀態：實作與本機驗證完成，原生範例 CI、L5 長測及最終同候選驗收待完成。
此階段在等待 L5 外部檢查／長測時準備，沒有改動正在量測的核心或量測工具。

## 交付內容

- `examples/sdk_offline.py` 包含 NumPy、PyTorch、mobile base、arm、processor、錄製、恢復
  七種公開 API 整合，每種都有預期行為斷言與資源清理。
- 既有離線範例移除私有入口、補上 finally／context manager、使用與 cwd 無關的設定路徑。
  修正 Jetbot 原地改寫 command 卻回報 PASS 的範例，現在以 CallbackResult.clamp 回報 CLAMP。
- Franka 範例改用位於 joint limits 內的合成觀測、核對真實 decision；移除未實際執行的
  模擬器流程和無 CPU conversion／即時頻率宣稱，保留離線 Tensor／batch 示範。
- 新增完整離線範例清單及 installed smoke runner，四格原生安裝 workflow 執行並保存結果。
  每個程序使用 `-I`、checkout 外臨時工作目錄、臨時 DAM_DATA_ROOT，確認匯入安裝的套件。
- [SDK 範例與現行介面](sdk-examples.md)、README、錄製指南、Library API 和專案 DAM skill
  同步公開生命週期、單位、明確 fallback 與歷史寫法；沒有加入相容 shim。

## 驗證與剩餘條件

本機 macOS arm64／Python 3.12 乾淨鎖定環境，11 個離線程式皆退出 0，包含七個 SDK 案例；
原始輸出保存 `logs/acceptance-l6-installed-examples.log`。Jetbot 的最後 CLAMP 回報修正另行
重跑。Python 3.11 乾淨安裝亦完成全部 11 個程式，紀錄 `logs/acceptance-l6-installed-py311.log`；
四格原生 CI 的範例步驟待執行。技能格式驗證通過。

`isaac_jetbot_lane_demo.py` 需要外部模擬器，不列為已通過。MCAP／A/B 僅使用臨時資料，
沒有安裝到 Console library 或接觸使用者錄製檔。沒有 PyPI 發布或實機操作。

L5 真實 8 小時穩定性結果尚未出爐；本報告不表示整體計畫完成。
