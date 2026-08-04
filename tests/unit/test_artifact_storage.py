from __future__ import annotations

from types import SimpleNamespace

import app.services.artifact_storage as artifact_storage_module
import pytest
from app.services.artifact_storage import ArtifactStorage


def make_storage(monkeypatch: pytest.MonkeyPatch, tmp_path, backend: str) -> ArtifactStorage:
    monkeypatch.setattr(
        artifact_storage_module,
        "get_settings",
        lambda: SimpleNamespace(artifact_storage_backend=backend),
    )
    storage = ArtifactStorage()
    storage.local_root = tmp_path / "artifacts"
    return storage


@pytest.mark.asyncio
async def test_put_bytes_uses_local_storage_without_s3(monkeypatch, tmp_path):
    storage = make_storage(monkeypatch, tmp_path, "local")
    monkeypatch.setattr(storage, "_put_s3", lambda *args: pytest.fail("S3 must not be called in local mode"))

    result = await storage.put_bytes("raw", "sources/file.txt", b"local data")

    assert result["storage_backend"] == "LOCAL_FALLBACK"
    assert result["storage_key"] == str((tmp_path / "artifacts/raw/sources/file.txt").resolve())
    assert (tmp_path / "artifacts/raw/sources/file.txt").read_bytes() == b"local data"


@pytest.mark.asyncio
async def test_put_bytes_falls_back_to_local_storage_after_s3_error(monkeypatch, tmp_path):
    storage = make_storage(monkeypatch, tmp_path, "s3")

    def fail_s3(*args):
        raise RuntimeError("S3 unavailable")

    monkeypatch.setattr(storage, "_put_s3", fail_s3)
    result = await storage.put_bytes("raw", "fallback.txt", b"fallback data")

    assert result["storage_backend"] == "LOCAL_FALLBACK"
    assert result["warning"] == "S3 unavailable"
    assert (tmp_path / "artifacts/raw/fallback.txt").read_bytes() == b"fallback data"


@pytest.mark.asyncio
async def test_get_bytes_reads_a_local_artifact(monkeypatch, tmp_path):
    storage = make_storage(monkeypatch, tmp_path, "local")
    stored = await storage.put_bytes("raw", "readable.txt", b"artifact contents")

    content = await storage.get_bytes("raw", stored["storage_key"], stored["storage_backend"])

    assert content == b"artifact contents"


@pytest.mark.asyncio
async def test_local_artifact_storage_rejects_path_traversal(monkeypatch, tmp_path):
    storage = make_storage(monkeypatch, tmp_path, "local")

    with pytest.raises(ValueError, match="Недопустимый путь artifact"):
        await storage.put_bytes("raw", "../outside.txt", b"must not escape")

    assert not (tmp_path / "outside.txt").exists()
