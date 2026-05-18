from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dam.experiments import list_experiments, run_experiment
from dam.services.routers.experiments import create_experiments_router


def test_experiment_registry_covers_all_thesis_rqs(tmp_path: Path) -> None:
    specs = list_experiments()
    assert [spec.rq for spec in specs] == ["RQ1", "RQ2", "RQ3", "RQ4", "RQ5"]

    runs = [
        ("l0-calibration", {"samples": 20, "outdir": str(tmp_path / "l0")}),
        ("boundary-scan", {"trials": 1, "outdir": str(tmp_path / "boundary")}),
        ("usability", {"trials_per_scenario": 5, "outdir": str(tmp_path / "use")}),
        ("latency-bench", {"frames": 1, "outdir": str(tmp_path / "latency")}),
        ("failure-record-quality", {"outdir": str(tmp_path / "quality")}),
    ]

    for experiment_id, params in runs:
        result = run_experiment(experiment_id, params)
        assert result.status == "success"
        assert result.rows
        assert any(path.endswith("results.csv") for path in result.artifacts)
        assert any(path.endswith(".svg") for path in result.artifacts)
        for artifact in result.artifacts:
            assert Path(artifact).is_file()


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
