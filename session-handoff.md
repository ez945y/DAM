# session-handoff.md — 會話交接摘要

> 最後更新: 2026-05-24 22:05

## 當前已驗證

- Live preview pipeline: WS coalescing + DOM bypass — 24 tests passing
- Guard status: `gGuardMap` 全量替換修正 — frontend tests 99/99 passing
- Risk log: notable-only filter + 10k capacity — 20 risk tests passing
- PM harness: CLAUDE.md + progress + handoff + init.sh + checklist 已建立

## 未驗證 / 待確認

- **Guard Status fix 未部署**: commit `49cea81` 已入，但前端需要 rebuild
  ```bash
  cd dam-console && npm run build
  ```
  然後重啟 `make dev`，確認 context switch 後 Guard Status 正確顯示只剩 L3

- **機器人微振盪**: MCAP 分析結論：
  - 關節最大 delta 0.16°，無爆衝（guard 全 PASS 是正確的）
  - 但 action sign changes ~60%——模型輸出在高頻振盪
  - **這不是 guard pipeline 問題，是模型輸出品質問題**
  - 可能解法：action smoothing（EMA filter on target positions）
  - 之前不抖可能是用了不同的 policy checkpoint 或不同的 control frequency

- **MCAP 回讀 Risk Log**: 用戶希望能從 MCAP 歷史 session 載入 risk data，未實作

## 本輪改動摘要

| Commit | 改動 |
|--------|------|
| `17f8641` | Live preview: WS coalescing + DOM CustomEvent bypass |
| `ac4cfc9` | PM harness: CLAUDE.md + progress + handoff + init.sh |
| `6a45c65` | 整合 harness/ → scripts/，精煉 CLAUDE.md |
| `49cea81` | Guard status: gGuardMap 全量替換 |
| `4d5993f` | Risk log: notable-only + 10k capacity |

## 下一步最佳動作

1. **驗證 Guard Status fix**: rebuild 前端，開 console，start → 等 context switch → 確認 L0-L2 變 STANDBY
2. **機器人抖動**: 如果確認是模型問題，考慮在 sink 層加 EMA smoothing；如果是之前某個版本不抖，用 git bisect 找出哪個 commit 造成的
3. **MCAP Risk Log 回讀**: 加一個 API endpoint 或 CLI 命令，從指定 MCAP 檔案載入 cycle 數據到 RiskLogService
4. **跑完整 `make test`**: 本輪只跑了 subset

## 命令速查

```bash
make dev                                           # 開發模式
make test                                          # 完整測試
cd dam-console && npm run build                    # 重建前端
python -m pytest tests/unit/ -x                    # 快速 unit test
python -m pytest tests/unit/test_live_preview.py -v # live preview tests
python -m pytest tests/unit/test_services.py -k risk -v # risk log tests
```

## MCAP 分析快速命令

```python
# 分析 session 的 joint oscillation
import msgpack, numpy as np
from mcap.reader import make_reader
f = open('data/robot/sessions/<session>.mcap', 'rb')
reader = make_reader(f)
for schema, ch, msg in reader.iter_messages(topics=['/dam/cycle']):
    data = msgpack.unpackb(msg.data, raw=False)
    # data keys: obs_joint_positions, action_positions, validated_positions,
    #            guard_results, fallback_triggered, was_clamped, was_rejected
```
