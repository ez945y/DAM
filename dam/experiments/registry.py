from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExperimentDef:
    id: str
    title: str
    rq: str
    description: str
    default_params: dict[str, Any]
    outputs: tuple[str, ...] = ("results.csv",)


@dataclass(frozen=True)
class ExperimentResult:
    id: str
    status: str
    elapsed_sec: float
    outdir: str
    rows: list[dict[str, Any]]
    summary: dict[str, Any]
    artifacts: list[str]


def _artifact_paths(outdir: Path, names: tuple[str, ...]) -> list[str]:
    return [str(outdir / name) for name in names if (outdir / name).exists()]


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    import csv

    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _svg_escape(value: object) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _write_line_svg(
    rows: list[dict[str, Any]],
    path: Path,
    *,
    title: str,
    series_key: str,
    x_key: str,
    y_key: str,
) -> None:
    width, height = 860, 420
    margin = 56
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row[series_key]), []).append(row)
    xs = [float(row[x_key]) for row in rows]
    ys = [float(row[y_key]) for row in rows]
    x_min, x_max = (min(xs), max(xs)) if xs else (0.0, 1.0)
    y_min, y_max = (min(ys), max(ys)) if ys else (0.0, 1.0)
    if math.isclose(x_min, x_max):
        x_max = x_min + 1.0
    if math.isclose(y_min, y_max):
        y_max = y_min + 1.0

    def sx(x: float) -> float:
        return margin + ((x - x_min) / (x_max - x_min)) * (width - margin * 2)

    def sy(y: float) -> float:
        return height - margin - ((y - y_min) / (y_max - y_min)) * (height - margin * 2)

    colors = ["#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#a78bfa", "#14b8a6"]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#0a0a0a"/>',
        f'<text x="{margin}" y="30" fill="#f0f0f0" font-family="Inter,Arial" font-size="18" font-weight="700">{_svg_escape(title)}</text>',
        f'<line x1="{margin}" y1="{height - margin}" x2="{width - margin}" y2="{height - margin}" stroke="#333"/>',
        f'<line x1="{margin}" y1="{margin}" x2="{margin}" y2="{height - margin}" stroke="#333"/>',
    ]
    for idx, (name, data) in enumerate(groups.items()):
        data = sorted(data, key=lambda r: float(r[x_key]))
        points = " ".join(f"{sx(float(r[x_key])):.1f},{sy(float(r[y_key])):.1f}" for r in data)
        color = colors[idx % len(colors)]
        parts.append(
            f'<polyline fill="none" stroke="{color}" stroke-width="2.5" points="{points}"/>'
        )
        for r in data:
            parts.append(
                f'<circle cx="{sx(float(r[x_key])):.1f}" cy="{sy(float(r[y_key])):.1f}" r="3" fill="{color}"/>'
            )
        parts.append(
            f'<text x="{width - margin - 180}" y="{margin + idx * 18}" fill="{color}" font-family="JetBrains Mono,monospace" font-size="12">{_svg_escape(name)}</text>'
        )
    parts.append(
        f'<text x="{width / 2}" y="{height - 12}" fill="#999" text-anchor="middle" font-family="Inter,Arial" font-size="12">{_svg_escape(x_key)}</text>'
    )
    parts.append(
        f'<text x="16" y="{height / 2}" fill="#999" transform="rotate(-90 16 {height / 2})" text-anchor="middle" font-family="Inter,Arial" font-size="12">{_svg_escape(y_key)}</text>'
    )
    parts.append("</svg>")
    path.write_text("\n".join(parts))


def _write_bar_svg(
    rows: list[dict[str, Any]],
    path: Path,
    *,
    title: str,
    label_key: str,
    value_key: str,
) -> None:
    width, height = 860, 420
    margin = 56
    values = [float(r[value_key]) for r in rows]
    max_value = max(values) if values else 1.0
    max_value = max(max_value, 1e-9)
    bar_w = (width - margin * 2) / max(len(rows), 1)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#0a0a0a"/>',
        f'<text x="{margin}" y="30" fill="#f0f0f0" font-family="Inter,Arial" font-size="18" font-weight="700">{_svg_escape(title)}</text>',
        f'<line x1="{margin}" y1="{height - margin}" x2="{width - margin}" y2="{height - margin}" stroke="#333"/>',
    ]
    for idx, row in enumerate(rows):
        value = float(row[value_key])
        h = (value / max_value) * (height - margin * 2)
        x = margin + idx * bar_w + bar_w * 0.15
        y = height - margin - h
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w * 0.7:.1f}" height="{h:.1f}" rx="3" fill="#3b82f6"/>'
        )
        parts.append(
            f'<text x="{x + bar_w * 0.35:.1f}" y="{y - 6:.1f}" fill="#dbeafe" text-anchor="middle" font-family="JetBrains Mono,monospace" font-size="11">{value:.2f}</text>'
        )
        parts.append(
            f'<text x="{x + bar_w * 0.35:.1f}" y="{height - margin + 18:.1f}" fill="#999" text-anchor="middle" font-family="Inter,Arial" font-size="10">{_svg_escape(row[label_key])}</text>'
        )
    parts.append("</svg>")
    path.write_text("\n".join(parts))


def _numeric_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        cleaned.append(
            {
                key: (float(value) if hasattr(value, "item") else value)
                for key, value in row.items()
                if key != "_raw"
            }
        )
    return cleaned


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _summarise_boundary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_scenario: dict[str, dict[str, Any]] = {}
    for row in rows:
        scenario = str(row["scenario"])
        entry = by_scenario.setdefault(
            scenario,
            {"points": 0, "max_interception_rate": 0.0, "total_trials": 0},
        )
        entry["points"] += 1
        entry["max_interception_rate"] = max(
            float(entry["max_interception_rate"]),
            float(row.get("interception_rate", 0.0)),
        )
        entry["total_trials"] += int(row.get("trials", 0))
    return {"scenarios": by_scenario}


def _run_boundary_scan(params: dict[str, Any], outdir: Path) -> ExperimentResult:
    from scripts import run_boundary_scan as scan

    trials = int(params.get("trials", 20))
    outdir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()

    rows: list[dict[str, Any]] = []
    rows += scan.scan_l1_joint_offset(trials)
    rows += scan.scan_l1_velocity_scale(trials)
    rows += scan.scan_l2_collision_distance(trials)
    rows += scan.scan_l2_timeout(trials)

    scan.write_csv(rows, outdir / "results.csv")
    scan.plot_results(rows, outdir)
    _write_line_svg(
        _numeric_rows(rows),
        outdir / "boundary_scan.svg",
        title="RQ2 Boundary Interception Curves",
        series_key="scenario",
        x_key="disturbance_value",
        y_key="interception_rate",
    )
    return ExperimentResult(
        id="boundary-scan",
        status="success",
        elapsed_sec=time.perf_counter() - started,
        outdir=str(outdir),
        rows=_numeric_rows(rows),
        summary=_summarise_boundary(rows),
        artifacts=_artifact_paths(
            outdir, ("results.csv", "boundary_scan.png", "boundary_scan.svg")
        ),
    )


def _summarise_latency(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    summary: dict[str, Any] = {
        "rows": len(rows),
        "profiles": sorted({str(row.get("config", "")) for row in rows if row.get("config")}),
    }
    by_fps: dict[str, Any] = {}
    for fps in sorted({float(row["target_fps"]) for row in rows if "target_fps" in row}):
        fps_rows = [row for row in rows if float(row.get("target_fps", -1)) == fps]
        p95s = sorted(float(row["p95_ms"]) for row in fps_rows)
        by_fps[str(fps)] = {
            "configs": len(fps_rows),
            "budget_ms": 1000.0 / fps,
            "max_p95_ms": max(p95s),
            "max_deadline_miss_rate": max(
                float(row.get("deadline_miss_rate", 0.0)) for row in fps_rows
            ),
        }
    if by_fps:
        summary["by_fps"] = by_fps
    return summary


def _run_latency_bench(params: dict[str, Any], outdir: Path) -> ExperimentResult:
    import numpy as np

    from scripts import run_latency_bench as bench

    frames = int(params.get("steps_per_config", params.get("frames", 500)))
    seed = int(params.get("seed", 42))
    realtime = bool(params.get("realtime", False))
    pace_seconds = 0.0 if realtime else float(params.get("pace_seconds_per_fps", 4.0))
    raw_fps = params.get("fps_values", params.get("fps", "10,20,50"))
    if isinstance(raw_fps, str):
        fps_values = [float(part.strip()) for part in raw_fps.split(",") if part.strip()]
    elif isinstance(raw_fps, list | tuple):
        fps_values = [float(value) for value in raw_fps]
    else:
        fps_values = [float(raw_fps)]
    if not fps_values:
        raise RuntimeError("fps_values must include at least one FPS")
    outdir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    rows: list[dict[str, Any]] = []
    for fps in fps_values:
        budget_ms = 1000.0 / fps
        rng = np.random.default_rng(seed)
        mode = "realtime" if realtime else f"paced {pace_seconds:g}s"
        message = (
            f"RQ4 latency-bench: start {fps:g} Hz, {frames} steps, "
            f"{budget_ms:.1f} ms budget, {mode}"
        )
        LOGGER.info(message)

        def progress(done: int, total: int) -> None:
            pct = (done / total) * 100.0
            progress_message = f"RQ4 latency-bench: {fps:g} Hz {done}/{total} steps ({pct:.0f}%)"
            LOGGER.info(progress_message)

        for stats in bench.run_frequency(
            frames,
            rng,
            budget_ms,
            realtime=realtime,
            pace_seconds=pace_seconds,
            progress=progress,
        ):
            stats.pop("_raw", None)
            rows.append({"target_fps": fps, "budget_ms": budget_ms, **stats})
        done_message = f"RQ4 latency-bench: done {fps:g} Hz"
        LOGGER.info(done_message)

    clean_rows = _numeric_rows(rows)
    bench.write_csv(clean_rows, outdir / "results.csv")
    bench.plot_results(clean_rows, outdir)
    return ExperimentResult(
        id="latency-bench",
        status="success",
        elapsed_sec=time.perf_counter() - started,
        outdir=str(outdir),
        rows=clean_rows,
        summary=_summarise_latency(clean_rows),
        artifacts=_artifact_paths(outdir, ("results.csv", "latency_bench.png")),
    )


def _run_l0_calibration(params: dict[str, Any], outdir: Path) -> ExperimentResult:
    from scripts import run_l0_calibration as cal

    normal_repo_id = str(
        params.get("normal_repo_id", params.get("hf_repo_id", "MikeChenYZ/soarm-fmb-v2"))
    )
    legal_repo_id = str(params.get("legal_repo_id", "MikeChenYZ/eval_soarm_fmb"))
    anomaly_repo_id = str(params.get("anomaly_repo_id", "MikeChenYZ/soarm-recover-failure"))
    sessions_dir: str | None = params.get("sessions_dir", "data/robot/sessions")
    ood_samples = int(params.get("ood_samples_per_scenario", 30))
    n_thresholds = int(params.get("n_thresholds", 40))
    seed = int(params.get("seed", 42))
    max_observations = params.get("max_observations_per_dataset", params.get("samples"))
    max_observations = int(max_observations) if max_observations is not None else None
    flow_epochs = int(params.get("flow_epochs", 50))
    nll_sigma = float(params.get("nll_sigma", 3.0))
    compare_ood_methods = _as_bool(params.get("compare_ood_methods", False))
    cache_dir = params.get("cache_dir", "data/experiments/l0_calibration/cache")
    cache_dir = str(cache_dir) if cache_dir else None
    vision_model = params.get("vision_model") or None
    vision_weight = float(params.get("vision_weight", 0.3))
    vision_camera = str(params.get("vision_camera", "top"))
    vision_subsample = int(params.get("vision_subsample", 30))
    runtime_model_path = params.get("runtime_model_path", "data/ood_models/ood_model.pt")
    runtime_model_path = str(runtime_model_path) if runtime_model_path else None
    outdir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()

    rows, summary = cal.run_calibration(
        normal_repo_id=normal_repo_id,
        legal_repo_id=legal_repo_id,
        anomaly_repo_id=anomaly_repo_id,
        sessions_dir=sessions_dir,
        ood_samples_per_scenario=ood_samples,
        n_thresholds=n_thresholds,
        seed=seed,
        max_observations_per_dataset=max_observations,
        flow_epochs=flow_epochs,
        nll_sigma=nll_sigma,
        compare_ood_methods=compare_ood_methods,
        cache_dir=cache_dir,
        vision_model=vision_model,
        vision_weight=vision_weight,
        vision_camera=vision_camera,
        vision_subsample=vision_subsample,
        runtime_model_path=runtime_model_path,
    )
    cal.write_csv(rows, outdir / "results.csv")
    cal.plot_results(rows, outdir)
    cal.plot_roc(summary, outdir)
    (outdir / "l0_calibration.svg").unlink(missing_ok=True)
    return ExperimentResult(
        id="l0-calibration",
        status="success",
        elapsed_sec=time.perf_counter() - started,
        outdir=str(outdir),
        rows=_numeric_rows(rows),
        summary=summary,
        artifacts=_artifact_paths(
            outdir, ("results.csv", "l0_calibration.png", "l0_roc_curve.png")
        ),
    )


def _run_usability(params: dict[str, Any], outdir: Path) -> ExperimentResult:
    from scripts import run_usability_study as usab

    trials = int(params.get("trials_per_scenario", 30))
    seed = int(params.get("seed", 42))
    outdir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()

    rows = usab.run_study(trials, seed)
    usab.write_csv(rows, outdir / "results.csv")
    usab.plot_results(rows, outdir)
    _write_bar_svg(
        rows,
        outdir / "usability_false_triggers.svg",
        title="RQ3 False Trigger Rate by Normal Scenario",
        label_key="scenario",
        value_key="false_trigger_rate",
    )
    total_trials = sum(int(r["trials"]) for r in rows) or 1
    return ExperimentResult(
        id="usability",
        status="success",
        elapsed_sec=time.perf_counter() - started,
        outdir=str(outdir),
        rows=_numeric_rows(rows),
        summary={
            "overall_false_trigger_rate": sum(float(r["false_triggers"]) for r in rows)
            / total_trials,
            "mean_success_rate": sum(float(r["success_rate"]) for r in rows) / len(rows),
        },
        artifacts=_artifact_paths(
            outdir, ("results.csv", "usability_false_triggers.png", "usability_false_triggers.svg")
        ),
    )


def _run_failure_record_quality(params: dict[str, Any], outdir: Path) -> ExperimentResult:
    from scripts import run_record_quality as recq

    trials = int(params.get("trials", 40))
    seed = int(params.get("seed", 42))
    outdir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()

    rows = recq.run_quality(trials, seed)
    recq.write_csv(rows, outdir / "results.csv")
    recq.plot_results(rows, outdir)
    _write_bar_svg(
        rows,
        outdir / "failure_record_quality.svg",
        title="RQ5 Failure Record Quality",
        label_key="metric",
        value_key="rate",
    )
    mean_rate = sum(float(r["rate"]) for r in rows) / len(rows) if rows else 0.0
    return ExperimentResult(
        id="failure-record-quality",
        status="success",
        elapsed_sec=time.perf_counter() - started,
        outdir=str(outdir),
        rows=_numeric_rows(rows),
        summary={"mean_quality_rate": mean_rate},
        artifacts=_artifact_paths(
            outdir,
            ("results.csv", "failure_record_quality.png", "failure_record_quality.svg"),
        ),
    )


_EXPERIMENTS: dict[
    str, tuple[ExperimentDef, Callable[[dict[str, Any], Path], ExperimentResult]]
] = {
    "boundary-scan": (
        ExperimentDef(
            id="boundary-scan",
            title="Boundary Precision Scan",
            rq="RQ2",
            description="Sweeps L1/L2 boundary stress levels and reports interception curves.",
            default_params={"trials": 20, "outdir": "data/experiments/boundary_scan"},
            outputs=("results.csv", "boundary_scan.png", "boundary_scan.svg"),
        ),
        _run_boundary_scan,
    ),
    "l0-calibration": (
        ExperimentDef(
            id="l0-calibration",
            title="L0 OOD Calibration",
            rq="RQ1",
            description=(
                "Evaluates DAM L0 features with Real-NVP trained on normal "
                "HuggingFace lerobot observations, then compares per-frame "
                "NLL for normal, legal-variation, and abnormal-A datasets."
            ),
            default_params={
                "normal_repo_id": "MikeChenYZ/soarm-fmb-v2",
                "legal_repo_id": "MikeChenYZ/eval_soarm_fmb",
                "anomaly_repo_id": "MikeChenYZ/soarm-recover-failure",
                "flow_epochs": 50,
                "nll_sigma": 3.0,
                "compare_ood_methods": False,
                "cache_dir": "data/experiments/l0_calibration/cache",
                "vision_model": "mobilenet_v3_large",
                "vision_weight": 0.3,
                "vision_camera": "top",
                "vision_subsample": 30,
                "runtime_model_path": "data/ood_models/ood_model.pt",
                "seed": 42,
                "outdir": "data/experiments/l0_calibration",
            },
            outputs=("results.csv", "l0_calibration.png", "l0_roc_curve.png"),
        ),
        _run_l0_calibration,
    ),
    "usability": (
        ExperimentDef(
            id="usability",
            title="Normal-Use False Trigger Study",
            rq="RQ3",
            description="Runs the real L0-L2 guard stack on benign legal-variation frames and measures genuine false-trigger and success rates.",
            default_params={
                "trials_per_scenario": 30,
                "seed": 42,
                "outdir": "data/experiments/usability",
            },
            outputs=(
                "results.csv",
                "usability_false_triggers.png",
                "usability_false_triggers.svg",
            ),
        ),
        _run_usability,
    ),
    "failure-record-quality": (
        ExperimentDef(
            id="failure-record-quality",
            title="Failure Record Quality",
            rq="RQ5",
            description="Drives real violating scenarios through the guard stack and scores the harvested failure records for completeness, classification, layer labels, and reuse readiness.",
            default_params={
                "trials": 40,
                "seed": 42,
                "outdir": "data/experiments/failure_record_quality",
            },
            outputs=(
                "results.csv",
                "failure_record_quality.png",
                "failure_record_quality.svg",
            ),
        ),
        _run_failure_record_quality,
    ),
    "latency-bench": (
        ExperimentDef(
            id="latency-bench",
            title="Guard Latency Benchmark",
            rq="RQ4",
            description=(
                "Profiles the Guard module in isolation across 10, 20, and 50 Hz budgets. "
                "The run excludes image preprocessing and policy inference and reports "
                "deadline miss rate for No Safety, Rule-based Safety, OOD-only, and Full RSMF."
            ),
            default_params={
                "fps_values": "10,20,50",
                "steps_per_config": 500,
                "realtime": False,
                "pace_seconds_per_fps": 4,
                "seed": 42,
                "outdir": "data/experiments/latency_bench",
            },
            outputs=("results.csv", "latency_bench.png"),
        ),
        _run_latency_bench,
    ),
}


def list_experiments() -> list[ExperimentDef]:
    return sorted((entry[0] for entry in _EXPERIMENTS.values()), key=lambda exp: exp.rq)


def run_experiment(experiment_id: str, params: dict[str, Any] | None = None) -> ExperimentResult:
    if experiment_id not in _EXPERIMENTS:
        raise KeyError(f"Unknown experiment: {experiment_id}")
    spec, fn = _EXPERIMENTS[experiment_id]
    merged = {**spec.default_params, **(params or {})}
    outdir = Path(str(merged.pop("outdir"))).resolve()
    return fn(merged, outdir)
