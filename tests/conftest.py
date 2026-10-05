"""Isolated storage and repeatable test libraries."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from photo_explorer.app import create_app
from tools.make_samples import make_samples


def pytest_configure(config):
    """Ensure the test scratch directory's parent exists on fresh checkouts."""
    (Path(__file__).resolve().parents[1] / ".work").mkdir(exist_ok=True)


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch, tmp_path):
    """Prevent every test from touching a real application cache."""
    home = tmp_path / "home"
    monkeypatch.setenv("PHOTO_EXPLORER_HOME", str(home))
    return home


@pytest.fixture(scope="session")
def sample_library(tmp_path_factory):
    """Generate shared read-only source samples once."""
    root = tmp_path_factory.mktemp("samples")
    make_samples(root, count=30, seed=7)
    return root


@pytest.fixture
def library(sample_library, tmp_path):
    """Give each test a private mutable copy of the source library."""
    root = tmp_path / "library"
    shutil.copytree(sample_library, root)
    return root


@pytest.fixture
def client(isolated_home):
    """Close the HTTP app and its worker threads at test teardown."""
    with TestClient(create_app()) as instance:
        yield instance


def wait_scan(client: TestClient, root_id: int) -> dict:
    """Wait a bounded time for both scan phases."""
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = client.get(f"/api/roots/{root_id}/scan").json()
        if not job["running"]:
            assert job["error"] is None, job
            return job
        time.sleep(.01)
    raise AssertionError("Scan did not finish within 15 seconds")


@pytest.fixture
def indexed(client, library):
    """Open through the public API and complete both indexing phases."""
    response = client.post("/api/roots", json={"path": str(library)})
    assert response.status_code == 200, response.text
    root_id = response.json()["root"]["id"]
    wait_scan(client, root_id)
    return root_id
