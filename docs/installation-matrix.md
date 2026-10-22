# 安裝與執行驗證矩陣

本表區分「release workflow 的建置目標」和「已執行驗證」，不因 wheel 可建出就宣稱
可部署。日期：2026-10-22；驗證候選版為 L3 `468830a` 加 L4 資源打包修正。

| 平台／Python | 建置／安裝 | 離線 SDK 執行 | 狀態 |
| --- | --- | --- | --- |
| macOS arm64／3.11.14 | 新建 venv，完整相依安裝，pip check 通過 | CPU 與 MPS 通過 | 本機原生驗證 |
| macOS arm64／3.12.12 | 新建 venv，完整相依安裝，pip check 通過 | CPU 與 MPS 通過 | 本機原生驗證 |
| Linux x86_64／3.11.17 | Docker amd64 完整建置／安裝通過 | CPU 通過 | 在 arm64 主機模擬，不能當成效能證據 |
| Linux x86_64／3.12.15 | Docker amd64 完整建置／安裝通過 | CPU 通過 | 同上 |
| macOS arm64／3.11.9、3.12.10 | 原生 CI 完整建置／安裝通過 | CPU 通過 | CI 39766957720 |
| Linux x86_64／3.11.17、3.12.15 | 原生 CI 完整建置／安裝通過 | CPU 通過 | CI 39766957720 |
| Linux arm64 | 既有 release 建置目標 | 本輪未驗證 | 實驗性執行目標 |
| Windows x86_64 | 既有 release 建置目標 | 本輪未驗證 | 實驗性執行目標 |
| Python 3.13 及後續版本 | 安裝 metadata 未限制上界 | 本輪未驗證 | 非已驗證組合 |
| CUDA | 未具備 CUDA 執行環境 | 未驗證 | 實驗性，不以 skip 計入通過 |

維護決策：目前經驗證的 Python 版本為 3.11／3.12，與既有 classifier 一致。
保留既有 wheel 建置目標與 Python 最低版本，但將沒有執行證據的目標明確列為實驗性，
不據此做發行相容性保證。後續 CI 結果應更新此表，不覆蓋舊失敗紀錄。

## 驗證內容

在 checkout 外，使用安裝環境的 `python -I scripts/acceptance/installed_smoke.py`
（腳本以絕對路徑提供），確認 dam／dam_rs 來自該環境，不由來源目錄遮蔽。
包含版本查詢、Rust 載入、preset／URDF 資源、實際 FK、NumPy CLAMP、公開結果／reset，
Tensor 的 requires-grad、dtype／device 保留及溢位拒絕。

CPU 必驗 float32、float64、bfloat16；MPS 驗證 float32／bfloat16。MPS 不宣稱
float64 支援。每個結果保留 Python、OS、架構、device、NumPy／torch／lerobot／pin／
proxsuite 版本；相依解析器在兩個 Python 版本選出的版本不同，沒有將其混稱同一環境。
記錄位於 `logs/acceptance-l4-*`。

## 安裝前提與打包修正

Python wheel 包含 `dam/_data/assets/presets.yaml` 和 SO101 URDF；seed asset 路徑
依套件位置解析，不再依賴目前工作目錄。安裝版使用者 preset 預設寫入
`~/.local/share/dam/presets.yaml`；設定 DAM_DATA_ROOT 時仍寫入該目錄，避免改寫套件資源。
原始碼 checkout 仍以 repository assets 為預設管理位置。

Debian/Ubuntu 的完整安裝包含 LeRobot，其 evdev 相依可能從來源編譯，需要
`build-essential` 與 `linux-libc-dev`；OpenCV 需要 `libgl1`／`libglib2.0-0`。
驗證 Dockerfile 明確安裝這些前提。首次乾淨 Linux 安裝因缺 Linux input headers 失敗，
補齊後重跑；沒有移除相依。鎖定驗收先安裝完整相依，再安裝候選 wheels 並以 pip check 檢查完整性。

`scripts/acceptance/Dockerfile.install` 從本次 Rust 來源建立 wheel，再在獨立 Python
image 安裝 Python 與 Rust wheels；執行目錄是 `/tmp`。Linux CPU 驗證使用 CPU torch
來源，不將無 CUDA 環境下的檢查描述為 CUDA 通過。
