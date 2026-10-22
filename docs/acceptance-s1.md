# S1 Safety Studio 驗收紀錄

日期：2026-10-22。基準：`b238cff` 加本輪未提交差異；對應
[驗收計畫](acceptance-plan.md) S1。驗收採瀏覽器連真實 API／臨時檔案、
後端故障注入與前端狀態回歸的組合，不宣稱每個例外都經瀏覽器手動操作。

## 發現與修正

從真實 API 載入的 plan 含 `frame: null`、`joint_names: null`，原先前端
只接受省略欄位，導致刷新時合法草稿被誤判損壞。已將前端型別與草稿驗證對齊
後端 nullable 契約，新增載入 → 未完成 JSON → remount 的回歸案例。
瀏覽器原本失敗的草稿在修正後成功恢復，未遺失內容，編譯仍被未完成 JSON 阻擋。

## 逐項證據

| S1 條件 | 驗證方式與結果 |
| --- | --- |
| 1 計畫／編譯／儲存 | 瀏覽器載入 demo-precision，原有 joint_range 與 precision_range 都顯示來源；保存新計畫、匯出 s1-compiled；同名新增明確拒絕覆寫。stackfile_library 測試驗證檔案內容不變 |
| 2 草稿／儲存錯誤 | 瀏覽器刷新恢復未完成 JSON；新增 nullable API 回應測試。SafetyStudio 測試涵蓋原始宣告文字、損壞、滿額、重試及元件重新索引 |
| 3 預覽與座標確認 | 瀏覽器修改／刷新後舊預覽消失且無套用入口；SafetyStudio 測試涵蓋 token 與座標確認不恢復 |
| 4 無效規則 | safety_authoring 後端測試與 RegionEditor／SafetyStudio 前端測試涵蓋 FK、frame、退化幾何、非有限值、單位／宣告錯誤 |
| 5 語言規則 | safety_authoring 測試涵蓋中英文量化規則及未解析子句全份拒絕；前端草稿不自動套用 |
| 6 切換收據 | 瀏覽器參數預覽 hot reload，套用後 version 1 與 active／saved hash 相同；rollback 曾顯示 pending，後續 API 收據確認 version 2 applied。結構變更預覽 restart required；後端測試驗證 stopped-save 不標 applied |
| 7 競態／rollback | 瀏覽器完成 rollback 預覽與套用；switching、switching_storage、switching_races 測試驗證停止／重啟、被取代、存檔錯誤與舊 token |
| 8 回放／回歸 | 瀏覽器真實 MCAP A/B：5/5 cycles、2 differences；完整區間重跑 5 assertions PASS。刻意 Must be PASS 得到 FAIL、2 mismatches。comparison 後端與 UI 測試涵蓋缺資料、指紋、損壞、ID 不符及過期回應 |
| 9 相鄰流程 | StackfileEditors、MCAP、RiskLog 前端全套通過；service_helpers、local_helpers、stackfile_library 的隔離程序／暫存檔測試通過 |

## 執行結果

- Python 開工基線：1102 unit tests passed。
- Safety／切換／回放／啟動 helper／Stackfile 針對性測試：180 passed。
- 修正後前端完整測試：20 suites、167 tests passed。
- 修正後 production build 與其 TypeScript 檢查通過。
- 隔離 server 的 Ruff 與 format check 通過。
- 首次新增前端案例因重複 option selector 失敗；限定 Saved plans 控制項後全套通過。

## 重現環境與證據保存

啟動 `.venv/bin/python -m tests.acceptance.safety_server`，再於 `dam-console/`
使用 `NEXT_PUBLIC_API_URL=http://127.0.0.1:18080 NEXT_PUBLIC_WS_URL=ws://127.0.0.1:18080 npm run dev -- --hostname 127.0.0.1 --port 13000`。
使用瀏覽器開啟 `http://127.0.0.1:13000/safety`。

測試 server 只掛載 production safety、library 與 MCAP routers；使用臨時根目錄，
沒有 sink、host 啟動或硬體控制路由。結束後臨時資料清除。兩個驗證服務均已停止。
本機證據保留於 `logs/acceptance-s1/`：草稿恢復、回放 PASS／FAIL 截圖及
switch-status／regressions JSON；此目錄不納入版本控制。

測試頁的 OFFLINE 與 control/status 404 來自刻意未掛載 host／telemetry，
不代表 Safety API 失聯。正式版仍有既有 upload-asset/NFT 與 Browserslist 警告；
Jest 的 useStackfileLibrary async act 警告尚待 S3 清理。未做實機驗證。

S1 功能門檻已通過；整體計畫尚未完成，下一階段為 S2／R04。
