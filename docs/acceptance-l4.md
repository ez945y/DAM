# L4／R08 乾淨安裝與相容性

日期：2026-10-22。狀態：本機與四格原生跨平台 CI 通過；依賴鎖定強化待重跑。
基準為 L3 `468830a` 加本階段差異。

## 已交付與本機結果

- 重現 Python wheel 缺少 preset／URDF。加入套件資源，seed 路徑脫離 cwd，
  安裝版使用者覆寫資料不再寫入 site-packages。新增 cwd 與安裝版寫入位置回歸。
- 建立公開安裝矩陣、獨立安裝 smoke 工具、Linux 安裝 Dockerfile 與四格原生 CI。
- macOS arm64 的 Python 3.11.14／3.12.12，新建環境完整安裝、相依檢查通過，
  CPU 與 MPS 的 dtype／device／requires-grad／溢位驗證通過。
- Linux x86_64 的 Python 3.11.17／3.12.15，在 arm64 主機的 amd64 模擬容器中，
  自行編譯 Rust wheel、完整安裝與 CPU smoke 通過；執行時停用網路。
- 每格均確認 import 來自安裝環境，並執行打包 URDF 的 FK，避免 checkout 遮蔽缺檔。
- 全套非硬體 Python **1319 passed in 31.55s**；完整 pre-commit、Ruff／format、
  docs-check 通過。原始記錄為 `logs/acceptance-l4-*`。

首次 Linux 安裝因 evdev 所需 input headers 缺失失敗，補上明確的系統前提後重跑。
沒有刪除相依或跳過 Tensor 案例。完整相依版本隨 smoke JSON 保存，
不同 Python 版本的 LeRobot／torch／NumPy 解析結果不混為同一環境。

## 剩餘驗收與限制

[原生 CI 39766957720](https://github.com/ez945y/DAM/actions/runs/39766957720)
四格已通過：macOS arm64 3.11.9／3.12.10、Ubuntu x86_64 3.11.17／3.12.15；
既有 DAM CI 同時通過。證據保存於 `logs/acceptance-l4/native-ci/`。

額外 Sonar 檢查指出驗收安裝未鎖定依賴及容器預設 root。新增四份完整版本／SHA-256
鎖定清單與建置工具鎖定清單；只有 evdev、feetech-servo-sdk 可從核對雜湊的來源建置，
其他套件只允許 wheel。完整相依先安裝，候選 wheels 再以 `--no-deps --no-build` 安裝，
最後 `pip check` 確認相依完整。容器執行改為 uid 10001。強化後原生 CI 待重跑，
完成前 L4 保持待驗收。程式複雜度等其餘 Sonar 建議另列審閱，不當作執行測試失敗。
Linux 模擬結果不作為效能證據。Windows、Linux arm64、Python 3.13+、CUDA 本輪
沒有執行證據，依 [支援矩陣](installation-matrix.md) 明確列為實驗性／未驗證。
沒有發布套件或連接硬體。
