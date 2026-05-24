# session-handoff.md — 會話交接摘要

> 最後更新: 2026-05-24 23:30

## 當前已驗證

- L0 OOD calibration: HuggingFace lerobot `MikeChenYZ/soarm-fmb-v2` as baseline (68 episodes, 59k obs)
- Calibration result: **PASS** — 97% detection, EER=2.7%, FPR=2.4%, all scenarios ≥95%
- MCAP cross-domain FPR: 22% — genuine distribution shift, not unit error
- Frontend: data source badge, cross-domain eval section with shift warning
- Guard status: `gGuardMap` 全量替換修正 — frontend tests 99/99 passing
- Risk log: notable-only filter + 10k capacity — 20 risk tests passing
- Unit tests: 518 passed, 0 failed

## 未驗證 / 待確認

- **MCAP cross-domain FPR 22%**: 代表 HF 訓練數據和本地機器人工作空間有分佈差異。Feature extractor L2 normalize 使得 deg2rad 無效。改善方向：
  1. 混合訓練（HF + MCAP 混合）
  2. Fine-tune: 在本地 MCAP 數據上微調
  3. 接受：22% 在部署前 fine-tune 是合理的 baseline

- **Guard Status fix 未部署**: commit `49cea81` 已入，前端需 rebuild

- **機器人微振盪**: 模型輸出品質問題，不是 guard pipeline 問題

- **前端 rebuild 後驗證**: experiments page 需要 rebuild 看 HF data source 和 cross-domain UI

## 本輪改動摘要

| Commit | 改動 |
|--------|------|
| `22e4bbb` | L0 calibration 改用 HF lerobot dataset as baseline |
| `66c2c02` | Frontend: HF data source badge + MCAP cross-domain eval UI |
| `4a49e82` | deg2rad conversion for HF data (scale-invariant, no metric change) |

## 下一步最佳動作

1. **降低 MCAP cross-domain FPR**: 混合 HF + MCAP 數據訓練，或增加 fine-tune 步驟
2. **前端驗證**: `cd dam-console && npm run build` → `make dev` → 開 experiments page 跑 L0 calibration
3. **跑完整 `make test`**: 本輪只跑了 subset

## 命令速查

```bash
make dev                                           # 開發模式
make test                                          # 完整測試
cd dam-console && npm run build                    # 重建前端
python scripts/run_l0_calibration.py               # 跑 L0 calibration (HF data)
python scripts/run_l0_calibration.py --hf-repo MikeChenYZ/soarm-fmb-v2  # 指定 HF repo
python -m pytest tests/unit/ -x                    # 快速 unit test
```
