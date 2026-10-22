# L3／R07 重載收據與 session 生命週期

日期：2026-10-22。基準為 L2 提交 `c20955e` 加本階段差異。

## 交付與驗收

- `request_reload`／`reload_receipt` 使用 Console 相同的 runtime 收據。
  pending 不稱已套用，下一驗證週期才 applied 或 rejected；回傳版本及原因。
- 結構變更與過期版本拒絕後保留原設定，重試可成功；重複請求不覆蓋 pending。
- 公開 SessionStatus、reset 與 switch_task；未知 task 在清理前拒絕。
- 重設使用最後已套用設定，先完成舊資源清理，再建立新 session。
  清除 guard／container／risk／命令與速度／觀測時間／EE override／last_results 歷史。
- 保留設定版本、已完成錄製檔與最近 100 筆收據；cycle ID 不倒退，generation 增加。
  關閉或重設取消 pending；close 可重複，reset 可重新開啟已關閉 session。
- 清理 timeout 不建立替代 runtime；重建失敗不丟失已套用版本與收據。
  兩者都顯示 recovery_required，阻擋求值，解除故障後 reset 可重試。

## 證據

完整非硬體 Python **1317 passed in 29.14s**，退出碼 0；
`logs/acceptance-l3-python.log`。session 針對性 11 項通過。
完整 pre-commit、Ruff／format、文件檢查與公開離線範例結果保存在
`logs/acceptance-l3-precommit.log`、`logs/acceptance-l3-docs.log`、
`logs/acceptance-l3-example.log`。

首次串接誤用了內部方法名稱，測試重現後改用現有 apply_pending_reload；
同時修正 constructor 呼叫的型別問題，再完整重跑。未放寬任何斷言。

本 API 仍要求單一控制執行緒序列化管理操作，不提供並行呼叫保證。
caller-provided solver 仍由 caller 擁有並重用；DAM 設定建立的 solver 會重新建立。
沒有擴張可熱重載範圍，未連硬體。跨平台與長時間資源驗證由 L4／L5 接續。
