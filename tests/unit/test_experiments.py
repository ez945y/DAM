from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dam.experiments import list_experiments, run_experiment
from dam.services.routers.experiments import create_experiments_router


def test_experiment_registry_covers_all_thesis_rqs(tmp_path: Path, monkeypatch) -> None:
    from scripts import run_l0_calibration as cal

    def fake_l0_run_calibration(**_kwargs):
        return (
            [
                {
                    "dataset": "normal_test",
                    "repo_id": "MikeChenYZ/soarm-fmb-v2",
                    "episode_index": 0,
                    "frame_index": 0,
                    "timestamp": 0.0,
                    "method": "real_nvp",
                    "score_name": "nll",
                    "score_value": 1.0,
                    "nll": 1.0,
                }
            ],
            {
                "dataset_stats": {
                    "normal_test": {"median": 1.0},
                    "legal_variation": {"median": 1.5},
                    "abnormal_a": {"median": 3.0},
                }
            },
        )

    monkeypatch.setattr(cal, "run_calibration", fake_l0_run_calibration)
    monkeypatch.setattr(
        cal,
        "plot_results",
        lambda _rows, outdir: (outdir / "l0_calibration.png").write_text("png"),
    )

    specs = list_experiments()
    assert [spec.rq for spec in specs] == ["RQ1", "RQ2", "RQ3", "RQ4", "RQ5"]

    runs = [
        ("l0-calibration", {"samples": 20, "outdir": str(tmp_path / "l0")}),
        ("boundary-scan", {"trials": 1, "outdir": str(tmp_path / "boundary")}),
        ("usability", {"trials_per_scenario": 5, "outdir": str(tmp_path / "use")}),
        ("failure-record-quality", {"outdir": str(tmp_path / "quality")}),
    ]

    for experiment_id, params in runs:
        result = run_experiment(experiment_id, params)
        assert result.status == "success"
        assert result.rows
        assert any(path.endswith("results.csv") for path in result.artifacts)
        assert any(path.endswith((".png", ".svg")) for path in result.artifacts)
        for artifact in result.artifacts:
            assert Path(artifact).is_file()


def test_latency_benchmark_profiles_guard_configs_per_fps(tmp_path: Path) -> None:
    result = run_experiment(
        "latency-bench",
        {
            "fps_values": "10,20",
            "steps_per_config": 2,
            "realtime": False,
            "pace_seconds_per_fps": 0,
            "outdir": str(tmp_path / "latency"),
        },
    )

    assert result.status == "success"
    assert len(result.rows) == 8
    assert {row["target_fps"] for row in result.rows} == {10.0, 20.0}
    assert {row["config"] for row in result.rows} == {
        "No Safety",
        "Rule-based Safety",
        "OOD-only",
        "Full RSMF",
    }
    assert "deadline_miss_rate" in result.rows[0]
    assert any(path.endswith("results.csv") for path in result.artifacts)
    assert not any(path.endswith(".svg") for path in result.artifacts)


def test_experiment_artifact_endpoint_serves_workspace_files(tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(create_experiments_router())
    client = TestClient(app)

    artifact = Path("data") / "tmp_test_artifact.svg"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>')
    try:
        response = client.get("/api/experiments/artifact", params={"path": str(artifact)})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/svg+xml")
    finally:
        artifact.unlink(missing_ok=True)


def test_experiment_artifacts_endpoint_lists_preview_files() -> None:
    app = FastAPI()
    app.include_router(create_experiments_router())
    client = TestClient(app)

    outdir = Path("data") / "experiments" / "tmp_preview"
    outdir.mkdir(parents=True, exist_ok=True)
    preview = outdir / "plot.png"
    svg = outdir / "plot.svg"
    csv = outdir / "results.csv"
    preview.write_text("png")
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>')
    csv.write_text("x\n1\n")
    try:
        response = client.get("/api/experiments/artifacts")
        assert response.status_code == 200
        paths = {item["path"] for item in response.json()["artifacts"]}
        assert str(preview) in paths
        assert str(svg) not in paths
        assert str(csv) not in paths
    finally:
        preview.unlink(missing_ok=True)
        svg.unlink(missing_ok=True)
        csv.unlink(missing_ok=True)
        outdir.rmdir()
