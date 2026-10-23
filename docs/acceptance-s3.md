# S3 短期候選版驗收

日期：2026-10-22。狀態：通過。範圍為 S1 Safety Studio 與 S2/R04 輸出契約；
長期 L1–L6 尚未完成，不代表整體交付完成。平台為本機 macOS arm64、Python 3.12。

## 同候選版結果

| 檢查 | 結果／證據 |
| --- | --- |
| Python unit／integration／safety／property | 1276 passed，退出碼 0；`logs/acceptance-s2-python-final.log` |
| Rust workspace | `cargo test --workspace` 退出碼 0；既有 image-writer 文件測試 1 項 ignored，未算成通過 |
| Frontend | 20 suites／167 tests passed，退出碼 0；`logs/acceptance-s3-jest-final.log` |
| 正式建置 | 退出碼 0；`logs/acceptance-s3-build-final.log` |
| TypeScript | `npx tsc --noEmit` 退出碼 0；`logs/acceptance-s3-types-final.log` |
| pre-commit 全部追蹤檔案 | whitespace、EOF、YAML、large-file、Ruff、format、mypy、Rust fmt／clippy 全通過；`logs/acceptance-s3-precommit-final.log` |
| 新增未追蹤 Python 檔案 | Ruff／format check 通過；mypy 全專案檢查亦包含 tests |
| 文件 | `make docs-check` 通過；`logs/acceptance-s3-docs-final.log` |
| 差異與候選版一致性 | `git diff --check` 通過；384 份程式碼檔案 SHA-256 指紋在測試後逐一比對無異動 |

程式碼指紋保存在 `logs/acceptance-candidate-source.json`，基準為 `b238cff` 加本輪
差異。Rust 程式碼本輪未修改，因此已完成的 Rust 驗證適用同一候選版。
瀏覽器與真實 API 證據見 [S1](acceptance-s1.md)，輸出故障注入見 [S2](acceptance-s2.md)。

## 修正與限制

- 真實 API 的 nullable 欄位不再使合法草稿恢復失敗。
- 所有 runtime 送出路徑都檢查有效性，sink 失敗不再留下成功歷史；fallback 缺前提不默默送出替代命令。
- Jest 排除 `.next` 建置產物，並修正非同步測試等待；最終測試不再出現原先兩個警告。
- 早期負載高時出現 Watchdog deadline 和 Jest timeout 失敗；最終固定版本依序重跑已通過，沒有放寬期限或斷言。
- 建置仍提示 caniuse-lite 資料較舊，以及 upload-asset route 動態檔案路徑的 NFT tracing 警告。
  兩者未阻止編譯、型別與測試；standalone 跨平台部署／資產封裝仍由 L4 驗證，這裡不宣告該項完成。
- 無實機、CUDA／MPS 或跨平台驗證；沒有啟用自動排程。

## 操作與回復

Safety Studio 操作見 [使用指南](safety-studio.md)；隔離驗證服務啟動方式見 S1 報告。
SDK fallback 行為見 [Library API](library-api.md)：非關節命令須明確指定 safe_action；
位置保持、退避和減速僅適用關節位置命令；Retreat 必須指定目標。

部署前保存目前使用的 Stackfile；Safety Studio 的參數更新依版本收據與 rollback 流程回復。
此輪沒有部署或修改實機設定。程式碼回復應針對本交付提交執行 revert，避免覆蓋其他工作。

下一階段：L1/R05 公開結構化結果與錯誤分類。
