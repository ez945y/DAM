# 最終候選版驗收紀錄

候選：`019552a`，2026-10-22。**仍待實際 8 小時穩定性完成，尚未結案。**

## 已通過的固定候選檢查

| 檢查 | 證據 |
| --- | --- |
| 非硬體 Python | 1337 passed in 25.31s，`logs/acceptance-candidate-python.log` |
| Rust workspace | cargo test 全部適用項目通過；1 個既有 ignored doc-test 不算通過，`logs/acceptance-candidate-rust.log` |
| Console 測試 | 20 suites／167 tests 通過，`logs/acceptance-candidate-frontend.log` |
| Console 正式建置／TypeScript | 退出 0，`logs/acceptance-candidate-build.log` |
| 完整 pre-commit | Ruff／format／mypy／Rust fmt／clippy 等通過，`logs/acceptance-candidate-precommit.log` |
| 文件與差異 | docs-check、git diff 檢查通過，驗證後原始碼未改動 |
| 原生乾淨安裝與離線範例 | Ubuntu x86_64／macOS arm64，各 Python 3.11／3.12，四格安裝與每格 11 個離線程式通過 |
| 外部品質檢查 | DAM CI 與 Sonar Code Analysis 通過 |

原生執行：[run 39775873326](https://github.com/ez945y/DAM/actions/runs/39775873326)。
下載紀錄為 `logs/acceptance-final-native-ci/`；程式／建置配置指紋為
`logs/acceptance-final-source-manifest.json`。Markdown 驗收文件與 mkdocs 導覽後續更新不視為 runtime 改動；原始 manifest 保留，文件配置差異另存 `logs/acceptance-final-documentation-delta.json`。

## 長時間與資源門檻

候選 `019552a` 的 fast／hybrid 各 10,000 cycles 已通過，p99 為 2.469／3.070 ms，
20 ms 超期率為 0.01%／0%。1,000 次含錄製生命週期通過，FD 3→3、threads 1→1，
無殘留 DAM worker。證據：`logs/acceptance-l5/run-vz2fwb4w/`、`run-6hbxpf_g/`。

當前指紋為 `82f9dae0d1beeceeae84f74b29a24e15a8586ca10c99154cb026b8836987a1de`。
舊 `run-o_jl6zg_` 已中斷，不能計入驗收。當前完整重跑為 `run-xcuuwqcp`，
於 2026-10-23 09:08:33 UTC 開始，最早約 17:08:36 UTC 完成；詳見下方重跑紀錄。
目前仍待驗收，不能以 probe／先前中斷／其他候選結果代替。
本聊天已設定每小時接續驗收（`dam-2`），完成後停用；既有廣泛自主改進排程維持暫停。

## 已知限制與警告

- 沒有實機、Isaac Lab、CUDA、Windows 或 Linux arm64 執行證據；平台聲明依
  [支援矩陣](installation-matrix.md)，沒有新增支援保證或以 skip 計入通過。
- Console 正式建置仍有既有 caniuse-lite 資料過時及 upload-asset runtime 路徑的 NFT tracing
  警告。這輪驗證開發／正式建置與頁面流程，沒有宣稱獨立 standalone 部署打包已驗證；
  upload 資料位置需由部署環境提供，這個警告不影響本輪 SDK wheel 驗證。
- Python／Rust wheels 是同一候選配對產物；沒有發布 PyPI 或合併 PR。
- 首次 Rust 開發安裝曾選到 shell 繼承的 Conda base，已在交接記錄；所有正式證據使用
  明確指定的 project／clean venv interpreter 與配對 wheel，沒有把混用環境結果算通過。

## 交付與回復

差異在分支 `codex/staged-sdk-acceptance`，
[draft PR #4](https://github.com/ez945y/DAM/pull/4)。各階段驗收報告與 session-handoff 保留
修正、失敗及重跑證據。交付使用 commit／來源指紋識別，不能只憑 0.7.0 版本字串辨識。

回復時以先前 checkpoint 的 Python 與 Rust 同時配對建置，或在獨立環境檢出先前 commit；
不要只降一半 binary／Python 套件。使用者錄製檔與 Console library 沒有被本輪驗收覆寫。

## 2026-10-23 耐久重跑

舊 `run-o_jl6zg_` 最後樣本為 3,780 秒，程序已不存在且沒有完成報告；
中斷原因與退出碼不明，保留 `interrupted.json`，不算通過。核心指紋未變。
完整 8 小時重跑於 2026-10-23 09:08:33 UTC 啟動，目錄 `logs/acceptance-l5/run-xcuuwqcp/`，
最早約 17:08:36 UTC（洛杉磯 10:08:36）完成。獨立背景 supervisor 32301 的紀錄位於
`logs/acceptance-l5/detached-20261023/`，結束時寫入 `exit.json`，需與最終 soak 報告交叉驗證。
本次仍待驗收，沒有調寬門檻。
