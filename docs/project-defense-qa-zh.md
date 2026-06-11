# DAM 專案安全定義與專業答辯筆記

這份筆記是給專案簡報、面試、論文口試或技術審查使用。重點不是把 DAM 說成「保證安全」的系統，而是清楚說明：它把 ML policy 輸出的動作放進一個可設定、可量測、可追溯的安全監控層，在動作進入硬體前做攔截、修正或拒絕。

---

## 一句話介紹

DAM, Detachable Action Monitor，是一個放在機器人 policy 和硬體之間的安全 middleware。它攔截每一次 action proposal，經過 L0 到 L3 的 guard pipeline，最後輸出 PASS、CLAMP 或 REJECT，並記錄每次決策的原因、延遲和上下文。

可以這樣講：

> 我的系統不是取代 robot controller，也不是宣稱形式化保證安全；它是一個 action-level 的 safety monitor。它讓 policy 的輸出在到達硬體前，先通過分佈外偵測、運動學限制、任務規則和硬體健康檢查。系統的核心價值是把 unsafe action 擋下來，並把原因和資料留下來讓人可以 debug。

---

## 這個專案中的「安全」定義

在 DAM 裡，安全不是抽象口號，而是每個 control cycle 都要滿足的條件集合。

### 1. Action safety

每個由 policy 產生的 action 必須滿足硬體和任務約束。例如：

- joint position 在上下限內
- joint velocity 不超過 motor limit
- joint acceleration 不造成劇烈跳動
- end-effector 留在 workspace 內
- gripper 動作符合目前任務階段
- 硬體溫度、電流、電壓、heartbeat 正常

如果違反但可以安全修正，就 CLAMP；如果無法可靠修正，就 REJECT。

### 2. Operational safety

系統在執行時不能只看單一步驟，也要考慮控制迴圈是否穩定：

- guard timeout 或 exception 時 fail-to-reject
- 延遲要低於控制週期 budget
- hot reload 只能套用完整有效的設定
- decision 和 telemetry 要可追溯

### 3. Epistemic safety

ML policy 在陌生狀態下可能仍然輸出看似合理的 action。L0 OOD guard 用 memory bank、Real-NVP NLL 或 Welford z-score 估計 observation 是否偏離訓練分佈。這不是完美保證，而是降低「模型不知道自己不知道」的風險。

### 4. Debuggable safety

DAM 的安全目標包含「看得見失敗」。每次 clamp 或 reject 都應該能回答：

- 哪一層 guard 觸發？
- 觸發哪個 boundary？
- 超過多少？
- 最後 action 被怎麼改？
- 當時前後的 observation/action 是什麼？
- 這次 guard latency 有沒有超過 deadline？

---

## 系統架構怎麼說

專業人士會想知道這是不是只是一些 if-else。可以用這個層次回答：

1. Policy 產生 proposed action。
2. DAM 在 action 進硬體前攔截。
3. L0 和 L1/L2/L3 從不同角度檢查同一個 action。
4. 每層產生 GuardResult：PASS、CLAMP、REJECT 或 FAULT。
5. Aggregator 取最保守結果：FAULT > REJECT > CLAMP > PASS。
6. 若只是可修正的 kinematic violation，系統求一個最小改動的 safe action。
7. 若無法可靠修正，進入 fallback，例如 hold position、retreat 或 emergency stop。
8. Console、risk log、MCAP replay 記錄整個 cycle。

可以這樣講：

> 我把安全設計成 defense-in-depth，不是單一規則。L1 負責物理限制，L2 負責任務語意，L3 負責硬體健康，L0 則處理 ML policy 在陌生資料分佈下的不確定性。最後不是平均投票，而是最保守決策勝出，因為安全系統中一個 reject 就足以阻止 action。

---

## 四層 Guard 的定義

### L0: OOD Detection

目的：判斷 observation 是否偏離 policy 熟悉的資料分佈。

方法：

- Memory Bank：抽 feature vector，算 nearest-neighbor distance，距離超過 threshold 就拒絕。
- Real-NVP：用 normalizing flow 估計 negative log-likelihood，NLL 過高代表異常。
- Welford z-score：線上估計 mean/variance，若最大 z-score 超過門檻則拒絕。

重點回答：

> L0 是 statistical guard，所以有 false positive 和 false negative。我不會把它說成保證安全，而是用 calibration set 設 threshold，例如 EER threshold，並用 temporal smoothing 避免單幀雜訊造成過多誤拒絕。

### L1: Physical Kinematics

目的：確保 action 符合 joint、velocity、acceleration、workspace、keep-out zone、orientation 等物理限制。

常見約束：

- joint position: lower_i <= q_i <= upper_i
- velocity: |q_target - q_current| / dt <= v_max
- acceleration: |v_t - v_{t-1}| / dt <= a_max
- workspace: end-effector 必須留在 box bounds
- keep-out zone: end-effector 必須避開禁區

重點回答：

> L1 是目前最接近 hard safety envelope 的層。對 joint/velocity/acceleration 這種可直接修正的限制，我會 clamp；對 workspace 或 CBF 類限制，會透過 QP 找到最小改動的 safe action。如果缺少 Jacobian 或 kinematic model，就不能假裝能精準修正，應該降級成 hold/reject。

### L2: Task Execution

目的：同一個動作在物理上可能合法，但在任務階段上不合理。例如還沒到抓取區就關 gripper。

範例：

- pick phase 只能 close gripper
- place phase 才能 open gripper
- 特定階段只能在某個 workspace 內移動
- node timeout 後不允許繼續執行

重點回答：

> L2 補足 L1 看不到的語意。L1 只知道物理限制，L2 知道任務階段。這也代表 L2 的品質取決於 boundary 設計，使用者必須定義正確的任務規則。

### L3: Hardware Monitoring

目的：檢查硬體狀態是否允許繼續執行。

範例：

- motor temperature
- current
- voltage
- force/torque
- following error
- heartbeat/watchdog
- host CPU/GPU/memory/temperature

重點回答：

> L3 不是預防所有硬體故障，而是對硬體回報的健康訊號做最後一道 gate。如果 sensor 本身壞掉或被 spoof，L3 無法憑空知道真相，所以它要搭配獨立硬體 E-stop 和 watchdog。

---

## 數學部分怎麼講

### 1. 最保守決策聚合

DAM 的決策不是投票，而是取 worst case：

```text
FAULT > REJECT > CLAMP > PASS
```

如果任何一層 REJECT，最後就 REJECT。這是安全系統常見的 conservative aggregation。

可以這樣回答：

> 我沒有用 majority vote，因為安全不是分類任務。只要有一個 guard 發現不可接受的 violation，就應該阻止 action。這避免一個嚴重風險被其他 pass 結果稀釋。

### 2. Joint limit clamp

對每個 joint：

```text
q_safe_i = min(max(q_nom_i, lower_i), upper_i)
```

這是 box constraint。它簡單、快、可解釋，但只能處理獨立 joint limit，無法處理耦合限制。

### 3. Velocity limit

若 action 是 target position：

```text
v_i = (q_target_i - q_current_i) / dt
```

限制：

```text
|v_i| <= v_max_i
```

若超過，系統把 target position 改成：

```text
q_safe_i = q_current_i + clip(v_i, -v_max_i, v_max_i) * dt
```

可以這樣回答：

> 速度限制不是只檢查 policy 有沒有提供 velocity，而是就算 action 是 target position，也可以用 dt 反推出 implied velocity。

### 4. Acceleration limit

加速度由相鄰 cycle 的 velocity 差估計：

```text
a_i = (v_i(t) - v_i(t-1)) / dt
```

限制：

```text
|a_i| <= a_max_i
```

這能抑制 policy 輸出的 jitter 或突然跳動。

### 5. QP 最小改動修正

當多個 L1 約束同時存在時，不能各自 clamp 後隨便套用，因為不同約束可能互相衝突。DAM 的 motion QP aggregator 把多個 constraint 合成一個 optimization：

```text
minimize    1/2 ||u - u_nom||^2 + 1/2 sum(lambda_i * delta_i^2)
subject to  lower_i - delta_lo_i <= u_i
            u_i - delta_up_i <= upper_i
            A_cbf u - delta_cbf <= b_cbf
            delta >= 0
```

其中：

- u_nom 是 policy 原本想執行的 action
- u 是修正後 action
- delta 是 slack variable
- lambda 是 slack penalty
- A_cbf u <= b_cbf 代表 workspace、keep-out、orientation 這類線性化後的 CBF constraint

可以這樣回答：

> 我用 QP 的原因是希望 action 被改得越少越好，同時滿足多個 safety constraints。這比單純 rule-by-rule clamp 更一致，因為所有限制會在同一個 optimization 裡一起考慮。

### 6. Control Barrier Function 直覺

對 workspace 邊界，可以定義安全函數：

```text
h(x) >= 0
```

例如上界：

```text
h_up = bound_max - ee_pos
```

離邊界越近，h 越小。CBF 會要求下一步仍保持安全：

```text
h(x_{k+1}) >= (1 - alpha * dt) h(x_k)
```

再用 Jacobian 線性化：

```text
ee_pos_next ≈ ee_pos + J (u - q)
```

最後得到 QP 可處理的線性不等式：

```text
A u <= b
```

可以這樣回答：

> CBF 的重點不是等撞到邊界才 reject，而是當系統接近邊界時就開始限制動作，讓它保持在 forward invariant 的安全集合內。不過這依賴 kinematic model 和 Jacobian 的準確性。

### 7. OOD threshold calibration

如果用 anomaly score s(x)，規則是：

```text
reject if s(x) > tau
```

tau 可以用 calibration set 決定，例如 Equal Error Rate：

```text
FPR(tau) = FNR(tau)
```

重點回答：

> threshold 不是憑感覺設的。理想上我會用 normal、legal variation、abnormal 三種資料做 calibration，看 AUROC、FPR/FNR，選一個符合任務風險偏好的 operating point。安全要求高時會選比較保守的 threshold，接受較高 false positive。

---

## Rule-based 方法的缺點怎麼回答

### 專業問題：這不就是 rule-based 嗎？有什麼貢獻？

建議回答：

> 是的，L1 到 L3 很大一部分是 rule-based 或 constraint-based，這是刻意的。因為安全層需要可解釋、可 audit、可預期，而不是再放一個黑箱模型。但我的貢獻不是單一 if-else，而是把 rule-based constraints、statistical OOD、QP-based clamp、fallback、telemetry 和 replay 整合成 action-level safety middleware。

補充：

- rule-based 適合 hard constraints
- ML/OOD 適合處理分佈偏移
- QP 適合處理多約束下的最小改動修正
- log/replay 適合安全事件分析

### 專業問題：rule-based 最大缺點是什麼？

建議回答：

> 最大缺點是 coverage 和 brittleness。規則只能擋住我有明確建模的風險，沒寫進 boundary 的風險不會自動被發現。例如 collision checking、人類意圖預測、sensor spoofing，都不是簡單 rule 可以完整處理。所以我會把 DAM 定位成 guardrail 和 observability layer，不是 complete safety certification。

可以主動承認：

- rule 太寬：unsafe action 可能通過
- rule 太窄：合法 action 被誤擋
- 規則之間可能衝突
- 需要 domain expert 設定 threshold
- 對未建模風險無能為力

### 專業問題：為什麼不用 end-to-end learning 直接學安全？

建議回答：

> 因為 safety-critical constraint 需要明確邊界和可驗證行為。End-to-end model 可以幫助感知或 anomaly detection，但我不希望最後一道 gate 完全不可解釋。DAM 的策略是：learned policy 負責 performance，safety monitor 負責 constraint enforcement。

---

## 延遲與 60 FPS 問題怎麼回答

### 先講控制週期 budget

不同頻率的時間 budget：

```text
10 Hz  -> 100 ms / cycle
20 Hz  -> 50 ms / cycle
50 Hz  -> 20 ms / cycle
60 Hz  -> 16.67 ms / cycle
100 Hz -> 10 ms / cycle
```

60 FPS 不只是平均低於 16.67 ms，還要看 p95、p99、max latency。安全系統最怕 tail latency。

### 專業問題：你的系統能跑 60 FPS 嗎？

建議回答：

> 我不會只用「能」或「不能」回答。要看啟用哪些 guard、硬體平台、是否包含 vision preprocessing、policy inference、I/O 和 logging。DAM 有 latency benchmark，會測 mean、p95、p99、max 和 deadline miss rate。60 Hz 的 budget 是 16.67 ms，所以我會用 p95/p99 是否低於這個 budget 來判斷，而不是只看平均值。

### 如果目前沒有實測 60 FPS 數據

誠實但專業地回答：

> 目前我會把 60 Hz 當成需要 benchmark 驗證的目標，不會直接宣稱保證可達。尤其 Full RSMF 如果包含 L0 vision OOD、QP、MCAP logging，可能會超過 16.67 ms。工程上可以把 hot path 移到 Rust、降低 L0 頻率、快慢路徑分離、非同步 logging，或在高頻控制迴圈中只跑 L1/L3，低頻跑 L0/L2。

### 延遲來源

常見 latency 來源：

- policy inference
- camera/image preprocessing
- L0 vision embedding 或 flow scoring
- QP solver
- Python GIL 和 GC
- hardware I/O
- MCAP/logging
- frontend telemetry websocket

DAM 的 RQ4 benchmark 目前定義是測 guard path：收到 proposed action 到產生 validated action，不包含 policy inference 和 image preprocessing。

### 60 FPS 策略

可以提出具體優化：

- L1 hard constraints 每 cycle 跑，因為它們便宜且最關鍵。
- L3 watchdog 每 cycle 或固定頻率跑。
- L0 OOD 不一定每 cycle 跑，可以每 N frame 跑一次，或只在 observation drift 明顯時跑。
- Vision OOD 可以使用 cached embedding、降解析度、輕量 backbone。
- Logging 改成 ring buffer + background flush。
- QP 只在接近邊界或有 violation candidate 時啟動。
- Rust data plane 降低 Python runtime jitter。
- 用 p95/p99 deadline miss rate 做驗收，而不是平均 FPS。

### 好的回答範例

> 如果只跑 deterministic L1/L3，60 Hz 比較可行；如果 Full RSMF 包含 vision OOD 和大量 logging，就需要實測，不能保證。我的設計會把 safety 分成 fast path 和 slow path：fast path 放 joint/velocity/acceleration、watchdog 這些必跑限制；slow path 放 OOD、replay、重型分析。這樣即使 slow path 掉幀，也不會阻塞核心控制安全。

---

## 專業人士可能會問的困難問題與回答

### Q1. 你怎麼定義「safe action」？

A:

> safe action 是在目前 observation、task phase 和 hardware status 下，滿足所有 active boundaries 的 action。它不代表全域絕對安全，而是相對於我明確建模的 constraints：joint limits、velocity/acceleration、workspace、task semantics 和 hardware health。若 action 可修正，就輸出最小改動的 clamped action；若不可可靠修正，就 reject 並進 fallback。

### Q2. 你能保證 robot 不會撞到人或物嗎？

A:

> 不能。DAM 預設沒有完整 collision checker，也沒有做人類存在偵測或人類 motion prediction。它能做的是限制 action、workspace、速度、硬體狀態，並支援加入 custom collision callback。若要用在 collaborative robot，需要額外整合 perception、collision checking、硬體 E-stop，以及符合 ISO/TS 15066 等標準的驗證。

### Q3. 如果 policy 輸出 NaN 或 Inf 會怎樣？

A:

> 這是我特別測的 adversarial case。NaN 很危險，因為在 Python 裡 `nan > limit` 會是 False，天真的 limit check 可能誤判 PASS。DAM 的 safety test 把 non-finite input 視為 violation，不允許 PASS。

### Q4. 如果 guard 自己出錯、timeout 或 exception 呢？

A:

> 原則是 fail-to-reject。安全層無法可靠判斷時，不應該讓 action 通過。exception、timeout、corrupt data 都應該導向 REJECT 或 FAULT，然後由 fallback engine 處理。

### Q5. 多個 guard 同時 clamp，怎麼合併？

A:

> 如果只是獨立 clamp，可能會互相覆蓋。DAM 對 L1 motion constraints 使用 QP aggregator，把 box bounds 和 CBF linear inequalities 合成一個 optimization，求離原 action 最近、同時滿足約束的 action。若結果是 reject 或 fault，則 reject/fault 優先於 clamp。

### Q6. 為什麼 workspace constraint 不是直接 clamp XYZ？

A:

> 因為 action 是 joint space，而 workspace 是 end-effector space。從 XYZ 回到 joint space 不是唯一解，也可能牽涉 singularity 和 joint limits。直接 clamp XYZ 不一定能產生合法 joint action。所以 DAM 使用 Jacobian 線性化和 CBF/QP，把 workspace 限制轉成 joint-space action constraint。

### Q7. QP 如果 infeasible 怎麼辦？

A:

> 目前 QP 使用 slack variables 讓問題盡量保持 feasible，並用 slack penalty 控制違反限制的成本。如果 solver failure 或結果不可信，就不能硬送到硬體，應該 reject 或 fallback。安全上不能把 solver failure 當 pass。

### Q8. CBF 的限制是什麼？

A:

> CBF 依賴模型和線性化。若 Jacobian 不準、dt 太大、速度太高或系統 dynamics 沒被建模，CBF 的保證會變弱。另外目前 workspace/keep-out 是幾何 constraint，不等於完整 collision avoidance。

### Q9. L0 OOD 有 false positive 怎麼處理？

A:

> 我會用 calibration set 選 threshold，並報告 FPR/FNR 或 AUROC。高風險任務可接受比較高 false positive，因為誤拒絕通常比執行危險 action 好。另外可以用 temporal smoothing，要求連續多幀異常才 reject，降低單幀 sensor noise 的影響。

### Q10. L0 OOD 有 false negative 怎麼辦？

A:

> 這就是為什麼不能只靠 OOD。L0 只能捕捉 distribution shift，不保證抓到所有 unsafe action。因此還需要 L1 physical constraints、L2 task rules、L3 hardware monitoring。這是 defense-in-depth 的理由。

### Q11. 你怎麼設定 threshold？

A:

> deterministic constraints 來自 robot spec、motor limit、workspace 設計和任務需求。OOD threshold 則用 validation/calibration data 設定，例如 EER threshold 或根據風險偏好選擇較低 FNR 的 operating point。threshold 設定後要用 boundary scan、latency bench 和 replay 驗證。

### Q12. 這跟傳統 safety controller 有什麼不同？

A:

> 傳統 controller 通常和特定硬體或任務綁在一起。DAM 的定位是 detachable middleware：policy 和 hardware driver 不必大改，透過 Stackfile 設定 boundaries，並提供 console、risk log、MCAP replay、experiment benchmark。它更像一個可插拔的 safety and observability layer。

### Q13. 如果 sensor 被 spoof 呢？

A:

> DAM 假設 observation 大致可信。若 sensor 被 spoof，guard 可能被騙。這是系統限制，所以高風險部署需要 sensor redundancy、hardware watchdog、out-of-band safety channel 和 physical E-stop。

### Q14. 你有做 adversarial testing 嗎？

A:

> 有。測試包含 NaN/Inf injection、boundary skimming、guard fault/timeout、aggregator leakage。目標是確保 hostile 或 degenerate input 不會讓 unsafe action 在所有 guard 都 PASS 的情況下進入 sink。

### Q15. 你的安全系統和 policy 誰負責最終安全？

A:

> Policy 負責產生高效能 action，DAM 負責在 action 進硬體前做 constraint enforcement。但真正安全部署還需要底層 controller、硬體 limit、E-stop、操作流程和人員監督。DAM 不是唯一安全來源，而是其中一層。

### Q16. 為什麼需要 MCAP/replay？安全不是擋下來就好嗎？

A:

> 對研究和工程來說，擋下來只是第一步。更重要的是知道為什麼擋、是否誤擋、policy 在什麼狀態下壞掉。MCAP/replay 可以保留 violation 前後 context，幫助改 boundary、重訓 policy、做 failure taxonomy。

### Q17. 你怎麼避免過度保守導致 robot 不會動？

A:

> 用 boundary scan 和 usability test。看 interception rate 隨 disturbance 增加的曲線，也看 benign legal variation 的 false trigger rate。如果合法動作常被擋，代表 boundary 太緊或 threshold 需要校準。安全系統不是越緊越好，而是要在任務成功率和風險之間找到可解釋的 operating point。

### Q18. 如果控制頻率提高，哪個 layer 會最先成為 bottleneck？

A:

> 通常是 L0 vision/OOD、QP solver、logging 或 Python runtime jitter。L1 的簡單 box constraints 很便宜，但 Full RSMF 在 60 Hz 下必須看 p95/p99。工程上我會把 critical fast path 保留在每 cycle，重型檢查降頻或非同步化。

### Q19. 為什麼不用 majority vote？

A:

> 因為安全決策不是分類投票。一個 guard 發現硬體過熱，其他 guard pass，最後也必須 reject。DAM 用 most-restrictive aggregation，避免嚴重風險被多數 pass 稀釋。

### Q20. 如果 boundary 設錯怎麼辦？

A:

> Boundary 設錯是 rule-based 系統的重要風險。DAM 用 schema validation、console visualization、risk log、boundary scan 和 replay 降低這個風險，但不能完全消除。正式部署前必須用 simulation、dry run、hardware readiness checklist 和 supervised testing 驗證。

---

## 可以主動講出的專案限制

講限制不是扣分，講得清楚反而加分。

- 不是 certified safety system。
- 沒有內建完整 collision checker。
- 不能單獨處理 human-robot collaboration safety。
- OOD 是 statistical，會有 false positive/false negative。
- Rule-based boundary coverage 取決於人是否定義對。
- 延遲必須在目標硬體上量測，不能只看開發機。
- Python fallback timing jitter 較高，real-time hot path 建議用 Rust 或獨立 controller。
- Sensor spoofing 和硬體故障需要 out-of-band safety。

可以這樣總結：

> 我不把 DAM 包裝成完整安全認證，而是把它定位成 research-grade safety middleware。它的強項是 action interception、constraint enforcement、fallback 和 observability；它的限制是未建模風險、sensor trust、collision checking 和 certified real-time guarantee。

---

## 30 秒口頭版

> DAM 是一個放在 robot policy 和硬體之間的 detachable safety monitor。它每個 control cycle 攔截 action，透過 L0 OOD、L1 physical kinematics、L2 task execution、L3 hardware health 四層 guard 判斷 PASS、CLAMP 或 REJECT。數學上，簡單 joint limit 用 box clamp，velocity/acceleration 用 dt 推導，workspace/keep-out/orientation 用 Jacobian 線性化成 CBF constraint，再用 QP 求最小改動的 safe action。它不是 certified safety system，也不保證 collision-free；它的價值是讓 unsafe action 在進硬體前被攔截，且所有決策可追蹤、可 replay、可 benchmark。

---

## 2 分鐘口頭版

> 我的專案 DAM 是 Detachable Action Monitor，目標是解決 ML policy 永遠會輸出下一個 action，但它不一定知道這個 action 對硬體或任務是否安全的問題。DAM 放在 policy 和 hardware sink 中間，每次 action 進硬體前都先通過 guard stack。
>
> 我把安全分成四層。L0 是 OOD detection，處理 policy 在陌生 observation 下不可靠的問題；L1 是 physical kinematics，檢查 joint limit、velocity、acceleration、workspace、keep-out zone；L2 是 task execution，處理任務階段的語意限制，例如不該抓的時候不能關 gripper；L3 是 hardware monitoring，檢查溫度、電流、電壓、watchdog 和 following error。最後 aggregator 不做 majority vote，而是最保守決策勝出，FAULT > REJECT > CLAMP > PASS。
>
> 數學上，簡單限制可以直接 clamp，但多個 L1 constraints 同時存在時，我用 QP 做最小改動修正：minimize action 和原 policy output 的距離，同時滿足 joint bounds 和 CBF 線性化後的 constraints。workspace 的部分會用 end-effector position 和 Jacobian 把安全集合 h(x) >= 0 轉成 A u <= b。這樣比單純 if-else 更一致，因為多個限制是在同一個 optimization 裡一起處理。
>
> 我也清楚知道限制。DAM 不是 safety certification，沒有內建完整 collision checker，也不能單獨保證 human safety。Rule-based 的缺點是 coverage 取決於 boundary 是否定義完整；OOD 也會有 false positive 和 false negative。延遲方面，60 Hz 只有 16.67 ms budget，所以不能只看 average，要看 p95/p99 和 deadline miss rate。工程上我會把 L1/L3 放 fast path，每 cycle 跑；L0 vision、logging、replay 這些重型工作可以降頻或非同步化。

---

## 簡報建議頁面

1. Problem: ML policy always outputs actions, even unsafe ones.
2. Definition of safety: action-level, operational, epistemic, debuggable.
3. Architecture: policy -> DAM guard stack -> fallback -> hardware.
4. Guard layers: L0/L1/L2/L3.
5. Math: clamp, velocity/acceleration, QP, CBF.
6. Latency: 10/20/50/60 Hz budgets, p95/p99, deadline miss.
7. Evaluation: boundary scan, OOD calibration, latency benchmark, adversarial tests.
8. Limitations: not certified, no built-in full collision checking, sensor trust.
9. Contribution: detachable safety middleware with observability and replay.
