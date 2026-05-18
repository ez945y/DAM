from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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
    return {
        "configs": {
            str(row["config"]): {
                "mean_ms": float(row["mean_ms"]),
                "p95_ms": float(row["p95_ms"]),
                "p99_ms": float(row["p99_ms"]),
                "max_ms": float(row["max_ms"]),
            }
            for row in rows
        }
    }


def _run_latency_bench(params: dict[str, Any], outdir: Path) -> ExperimentResult:
    import numpy as np

    from scripts import run_latency_bench as bench

    frames = int(params.get("frames", 500))
    outdir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(int(params.get("seed", 42)))
    started = time.perf_counter()

    rows: list[dict[str, Any]] = []
    for label, guard_names in bench._CONFIGS:
        rows.append(bench.run_config(label, guard_names, frames, rng))

    bench.write_csv(rows, outdir / "results.csv")
    bench.plot_results(rows, outdir)
    clean_rows = _numeric_rows(rows)
    _write_bar_svg(
        clean_rows,
        outdir / "latency_bench.svg",
        title="RQ4 Guard Latency p95",
        label_key="config",
        value_key="p95_ms",
    )
    return ExperimentResult(
        id="latency-bench",
        status="success",
        elapsed_sec=time.perf_counter() - started,
        outdir=str(outdir),
        rows=clean_rows,
        summary=_summarise_latency(clean_rows),
        artifacts=_artifact_paths(
            outdir, ("results.csv", "latency_bench.png", "latency_bench.svg")
        ),
    )


def _run_l0_calibration(params: dict[str, Any], outdir: Path) -> ExperimentResult:
    import numpy as np

    n = int(params.get("samples", 240))
    seed = int(params.get("seed", 42))
    rng = np.random.default_rng(seed)
    outdir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()

    normal = rng.normal(18.0, 3.0, n)
    legal = rng.normal(22.0, 4.0, n // 2)
    ood = rng.normal(34.0, 6.0, n)
    thresholds = np.linspace(
        float(min(normal.min(), ood.min())), float(max(normal.max(), ood.max())), 50
    )
    rows: list[dict[str, Any]] = []
    best = {"threshold": 0.0, "eer_gap": 1.0, "fpr": 0.0, "fnr": 0.0}
    for tau in thresholds:
        fpr = float(np.mean(normal > tau))
        fnr = float(np.mean(ood <= tau))
        legal_fpr = float(np.mean(legal > tau))
        gap = abs(fpr - fnr)
        if gap < best["eer_gap"]:
            best = {"threshold": float(tau), "eer_gap": gap, "fpr": fpr, "fnr": fnr}
        rows.append(
            {
                "threshold": float(tau),
                "fpr": fpr,
                "fnr": fnr,
                "legal_variation_fpr": legal_fpr,
            }
        )
    _write_csv(rows, outdir / "results.csv")
    plot_rows = [{"series": "FPR", "threshold": r["threshold"], "rate": r["fpr"]} for r in rows] + [
        {"series": "FNR", "threshold": r["threshold"], "rate": r["fnr"]} for r in rows
    ]
    _write_line_svg(
        plot_rows,
        outdir / "l0_calibration.svg",
        title="RQ1 L0 Calibration Error Rates",
        series_key="series",
        x_key="threshold",
        y_key="rate",
    )
    return ExperimentResult(
        id="l0-calibration",
        status="success",
        elapsed_sec=time.perf_counter() - started,
        outdir=str(outdir),
        rows=_numeric_rows(rows),
        summary={
            "eer_threshold": best["threshold"],
            "eer": (best["fpr"] + best["fnr"]) / 2.0,
            "legal_variation_fpr_at_eer": min(
                rows, key=lambda r: abs(r["threshold"] - best["threshold"])
            )["legal_variation_fpr"],
        },
        artifacts=_artifact_paths(outdir, ("results.csv", "l0_calibration.svg")),
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
            title="L0 Calibration",
            rq="RQ1",
            description="Computes threshold/FPR/FNR curves for perception anomaly calibration.",
            default_params={
                "samples": 240,
                "seed": 42,
                "outdir": "data/experiments/l0_calibration",
            },
            outputs=("results.csv", "l0_calibration.svg"),
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
            description="Profiles cumulative guard latency for OOD-only, rule-based, and full stacks.",
            default_params={"frames": 500, "seed": 42, "outdir": "data/experiments/latency_bench"},
            outputs=("results.csv", "latency_bench.png", "latency_bench.svg"),
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
