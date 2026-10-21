"""Upload paths must reject untrusted components before touching storage."""

import asyncio
import json
import os
import time
from types import SimpleNamespace
from urllib.parse import unquote

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.backend.routes import upload


INVALID_IDS = [
    "../escape", r"..\escape", "a/b", r"a\b", "/absolute", "C:/absolute",
    r"C:\absolute", "C:relative", r"\\server\share", "//server/share",
    unquote("%2e%2e%2fescape"), unquote("%2e%2e%5cescape"),
    ".", "..", "has.dot", " leading", "trailing ", "has space", "\t", "\n",
    "", "a" * 65, "ａｂｃ", "a／b", "a＼b", "a∕b", "a\u2028b", "a\x00b",
]
VALID_IDS = ["V1StGXR8_Z5jdHi6B-myT", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "a", "A_9-" * 16]
INVALID_FILENAMES = [
    "../resume.pdf", r"..\resume.pdf", "folder/resume.pdf", r"folder\resume.pdf",
    "/resume.pdf", r"C:\resume.pdf", "C:resume.pdf", r"\\server\resume.pdf",
    ".", "..", "", "   ", "a" * 256, "resume\x00.pdf", "resume\n.pdf",
    "resume\r.pdf", "resume\t.pdf", "resume\x7f.pdf", "resume\x85.pdf",
    "resume\u202e.pdf",
]
USER = SimpleNamespace(id=11, tenant_id=21)


def _helper(name):
    helper = getattr(upload, name, None)
    assert callable(helper), f"Missing upload security helper: {name}"
    return helper


def _snapshot(root):
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
        for path in root.rglob("*")
    }


@pytest.fixture
def upload_client(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setattr(upload, "CHUNK_STORAGE_DIR", storage)
    app = FastAPI()
    app.include_router(upload.router)
    app.dependency_overrides[upload.require_active_subscription] = lambda: USER
    app.dependency_overrides[upload.get_current_user] = lambda: USER
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, storage


def _post_chunk(client, upload_id="safe-upload", filename="resume.pdf", index=0, content=b"%PDF-1.7\nresume"):
    return client.post(
        "/api/upload/chunk",
        data={"upload_id": upload_id, "filename": filename, "chunk_index": index, "total_chunks": 2},
        files={"chunk": ("chunk.bin", content, "application/octet-stream")},
    )


@pytest.mark.parametrize("upload_id", INVALID_IDS)
def test_upload_id_rejects_every_non_ascii_or_path_component(upload_id):
    with pytest.raises(HTTPException) as error:
        _helper("_validate_upload_id")(upload_id)
    assert error.value.status_code == 400
    assert upload_id not in str(error.value.detail) or not upload_id


@pytest.mark.parametrize("upload_id", VALID_IDS)
def test_upload_id_preserves_nanoid_uuid_and_bounds(upload_id):
    assert _helper("_validate_upload_id")(upload_id) == upload_id


@pytest.mark.parametrize("filename", INVALID_FILENAMES)
def test_client_filename_rejects_paths_and_control_characters(filename):
    with pytest.raises(HTTPException) as error:
        _helper("_safe_client_filename")(filename)
    assert error.value.status_code == 400


@pytest.mark.parametrize("filename", ["resume.pdf", "Jane Doe.docx", "résumé_日本語.pdf", "a" * 251 + ".pdf"])
def test_client_filename_preserves_safe_display_names(filename):
    assert _helper("_safe_client_filename")(filename) == filename


@pytest.mark.parametrize("part", ["../outside", "../storage-sibling/file", ".."])
def test_contained_path_rejects_escape_without_creating_anything(tmp_path, part):
    storage = tmp_path / "storage"
    with pytest.raises(HTTPException) as error:
        _helper("_contained_path")(storage, part)
    assert error.value.status_code == 400
    assert list(tmp_path.iterdir()) == []


def test_contained_path_rejects_absolute_sibling(tmp_path):
    with pytest.raises(HTTPException):
        _helper("_contained_path")(tmp_path / "storage", str(tmp_path / "outside"))
    assert list(tmp_path.iterdir()) == []


def test_contained_path_returns_resolved_child_without_creating_it(tmp_path):
    path = _helper("_contained_path")(tmp_path / "storage", "upload", "chunk_0000")
    assert path == tmp_path.resolve() / "storage" / "upload" / "chunk_0000"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("upload_id", ["../escape", r"..\escape", "a/b", r"a\b", ".", "..", "has.dot", "bad id", "", "a" * 65, "ａｂｃ", "a／b"])
def test_initial_invalid_id_creates_no_files_inside_or_outside_storage(upload_client, tmp_path, upload_id):
    client, storage = upload_client
    response = _post_chunk(client, upload_id=upload_id)
    assert _snapshot(tmp_path) == {}
    assert response.status_code in ({400, 422} if not upload_id else {400})
    assert str(storage) not in response.text


def test_initial_absolute_id_cannot_write_to_a_sibling(upload_client, tmp_path):
    client, _ = upload_client
    response = _post_chunk(client, upload_id=str(tmp_path / "outside"))
    assert _snapshot(tmp_path) == {}
    assert response.status_code == 400


@pytest.mark.parametrize("filename", INVALID_FILENAMES)
def test_initial_invalid_filename_creates_no_files(upload_client, tmp_path, filename):
    client, _ = upload_client
    response = _post_chunk(client, filename=filename)
    assert _snapshot(tmp_path) == {}
    assert response.status_code in ({400, 422} if not filename else {400})


@pytest.mark.parametrize("operation", ["chunk-zero", "later-chunk", "finalize", "cancel"])
def test_foreign_active_upload_is_not_found_and_unchanged(upload_client, tmp_path, operation):
    client, storage = upload_client
    folder = storage / "foreign-upload"
    folder.mkdir(parents=True)
    (folder / "metadata.json").write_text(json.dumps({"tenant_id": 999, "filename": "owner.pdf", "total_chunks": 2}))
    (folder / "chunk_0000").write_bytes(b"owner data")
    before = _snapshot(tmp_path)
    if operation.startswith("chunk") or operation == "later-chunk":
        response = _post_chunk(client, upload_id="foreign-upload", index=int(operation == "later-chunk"))
    elif operation == "finalize":
        response = client.post("/api/upload/finalize", json={"upload_id": "foreign-upload", "filename": "owner.pdf", "total_chunks": 1})
    else:
        response = client.delete("/api/upload/cancel/foreign-upload")
    assert response.status_code == 404
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("upload_id", ["orphaned-upload", "assembled"])
def test_existing_directory_without_ownership_cannot_be_claimed(upload_client, tmp_path, upload_id):
    client, storage = upload_client
    folder = storage / upload_id
    folder.mkdir(parents=True)
    (folder / "existing-owner-file.bin").write_bytes(b"existing upload data")
    before = _snapshot(tmp_path)
    response = _post_chunk(client, upload_id=upload_id)
    assert response.status_code == 404
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("filename", INVALID_FILENAMES)
def test_finalize_rejects_unsafe_filename_before_writing(upload_client, tmp_path, filename):
    client, _ = upload_client
    assert _post_chunk(client).status_code == 200
    before = _snapshot(tmp_path)
    response = client.post("/api/upload/finalize", json={"upload_id": "safe-upload", "filename": filename, "total_chunks": 1})
    assert _snapshot(tmp_path) == before
    assert response.status_code == 400


@pytest.mark.parametrize("upload_id", VALID_IDS + ["assembled"])
def test_finalize_uses_server_path_and_retains_owner_metadata(upload_client, monkeypatch, upload_id):
    client, storage = upload_client
    filename = "résumé_日本語.pdf"
    content = b"%PDF-1.7\nresume"
    scans = []
    monkeypatch.setattr(upload, "validate_and_scan", lambda data, name: scans.append((data, name)))
    assert _post_chunk(client, upload_id=upload_id, filename=filename, content=content).status_code == 200
    response = client.post("/api/upload/finalize", json={"upload_id": upload_id, "filename": filename, "total_chunks": 1})
    assert response.status_code == 200
    files = [path for path in storage.rglob("*") if path.is_file()]
    assembled = [path for path in files if path.suffix == ".bin"]
    assert len(assembled) == 1
    assert filename not in assembled[0].name
    assert assembled[0].read_bytes() == content
    assert scans == [(content, filename)]
    assert upload.assert_upload_owned(upload_id, USER)["filename"] == filename
    assert not (storage / upload_id).exists()
    with pytest.raises(HTTPException) as error:
        upload.assert_upload_owned(upload_id, SimpleNamespace(tenant_id=999))
    assert error.value.status_code == 404
    response = client.delete(f"/api/upload/cancel/{upload_id}")
    assert response.status_code == 200
    assert not any(path.is_file() for path in storage.rglob("*"))


@pytest.mark.parametrize("operation", ["finalize", "cancel"])
@pytest.mark.parametrize("upload_id", INVALID_IDS)
def test_malformed_id_is_rejected_before_finalize_or_cancel_io(upload_client, tmp_path, upload_id, operation):
    client, _ = upload_client
    if operation == "finalize":
        response = client.post("/api/upload/finalize", json={"upload_id": upload_id, "filename": "resume.pdf", "total_chunks": 1})
        assert response.status_code == 400
    else:
        # Call the endpoint with the decoded value; slash-containing path parameters
        # are otherwise rejected by the router before the handler is reached.
        with pytest.raises(HTTPException) as error:
            asyncio.run(upload.cancel_upload(upload_id=upload_id, current_user=USER))
        assert error.value.status_code == 400
    assert _snapshot(tmp_path) == {}


def test_containment_resolves_symlinks_before_allowing_io(tmp_path):
    storage = tmp_path / "storage"
    storage.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = storage / "linked-upload"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Creating test symlinks is unavailable: {error}")
    with pytest.raises(HTTPException) as error:
        _helper("_contained_path")(storage, "linked-upload", "chunk_0000")
    assert error.value.status_code == 400
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("endpoint", ["/api/analyze/batch-chunked", "/api/analyze/batch-stream"])
def test_finalize_then_analyze_reads_owner_file_and_hides_foreign_upload(
    auth_client, upload_client, tmp_path, monkeypatch, endpoint,
):
    foreign_client, storage = upload_client
    # Exceed only the analysis route's 10 MiB limit, not the chunk/finalize limits.
    # Its size error proves the real assembled bytes were located and read,
    # without running the unrelated LLM/parser/persistence pipeline.
    content = b"%PDF-1.7\n" + b"x" * (10 * 1024 * 1024)
    monkeypatch.setattr(upload, "validate_and_scan", lambda data, name: None)
    for owner, upload_id in [(auth_client, "owned-finalized"), (foreign_client, "foreign-finalized")]:
        assert _post_chunk(owner, upload_id=upload_id, filename="original.pdf", content=content).status_code == 200
        finalized = owner.post("/api/upload/finalize", json={"upload_id": upload_id, "filename": "original.pdf", "total_chunks": 1})
        assert finalized.status_code == 200
        assert not (storage / upload_id).exists()
    before = _snapshot(tmp_path)
    response = auth_client.post(endpoint, data={
        "upload_ids": ["owned-finalized", "foreign-finalized"],
        "filenames": ["display-alias.pdf", "foreign-alias.pdf"],
        "job_description": "Python engineer with five years of backend development experience. " * 10,
    })
    assert response.status_code == 200, response.text
    if endpoint.endswith("batch-stream"):
        failures = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: {")]
        failures = [event for event in failures if event["event"] == "failed"]
    else:
        failures = response.json()["failed"]
    assert len(failures) == 2
    owned = next(item for item in failures if "too large" in item["error"])
    assert owned["filename"] == "original.pdf"
    assert any("not found" in item["error"] for item in failures)
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("operation", ["later-chunk", "finalize", "cancel"])
def test_foreign_finalized_upload_cannot_be_replaced_or_deleted(upload_client, tmp_path, monkeypatch, operation):
    client, _ = upload_client
    monkeypatch.setattr(upload, "validate_and_scan", lambda data, name: None)
    assert _post_chunk(client).status_code == 200
    assert client.post("/api/upload/finalize", json={"upload_id": "safe-upload", "filename": "resume.pdf", "total_chunks": 1}).status_code == 200
    stranger = SimpleNamespace(id=999, tenant_id=999)
    client.app.dependency_overrides[upload.require_active_subscription] = lambda: stranger
    client.app.dependency_overrides[upload.get_current_user] = lambda: stranger
    before = _snapshot(tmp_path)
    if operation == "later-chunk":
        response = _post_chunk(client, index=1)
    elif operation == "finalize":
        response = client.post("/api/upload/finalize", json={"upload_id": "safe-upload", "filename": "resume.pdf", "total_chunks": 1})
    else:
        response = client.delete("/api/upload/cancel/safe-upload")
    assert response.status_code == 404
    assert _snapshot(tmp_path) == before


def test_cleanup_expires_only_stale_assembled_files_and_metadata(upload_client, monkeypatch):
    client, storage = upload_client
    monkeypatch.setattr(upload, "validate_and_scan", lambda data, name: None)
    for upload_id in ["stale-upload", "fresh-upload"]:
        assert _post_chunk(client, upload_id=upload_id).status_code == 200
        assert client.post("/api/upload/finalize", json={"upload_id": upload_id, "filename": "resume.pdf", "total_chunks": 1}).status_code == 200
    stale_time = time.time() - 25 * 60 * 60
    stale_files = [path for path in storage.rglob("*") if path.is_file() and "stale-upload" in path.name]
    fresh_files = [path for path in storage.rglob("*") if path.is_file() and "fresh-upload" in path.name]
    assert len(stale_files) == len(fresh_files) == 2
    for path in stale_files:
        os.utime(path, (stale_time, stale_time))
    upload._cleanup_old_chunks()
    assert all(not path.exists() for path in stale_files)
    assert all(path.exists() for path in fresh_files)


@pytest.mark.parametrize("endpoint", ["/api/analyze/batch-chunked", "/api/analyze/batch-stream"])
def test_successful_analysis_consumes_assembled_bytes_and_metadata(
    auth_client, upload_client, mock_hybrid_pipeline, monkeypatch, endpoint,
):
    from app.backend.db import database
    from app.backend.routes import analyze

    # Streaming uses its own sessions; point that external DB boundary at the
    # same real in-memory database as the authenticated route fixtures.
    monkeypatch.setattr(analyze, "SessionLocal", database.SessionLocal)
    _, storage = upload_client
    content = b"John Doe\nSoftware Engineer\njohn@example.com\nSKILLS\nPython, React\n"
    assert _post_chunk(auth_client, filename="resume.txt", content=content).status_code == 200
    assert auth_client.post("/api/upload/finalize", json={"upload_id": "safe-upload", "filename": "resume.txt", "total_chunks": 1}).status_code == 200
    response = auth_client.post(endpoint, data={
        "upload_ids": ["safe-upload"], "filenames": ["client-alias.txt"],
        "job_description": "Python engineer with five years of backend development experience. " * 10,
    })
    assert response.status_code == 200, response.text
    if endpoint.endswith("batch-stream"):
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: {")]
        done = next(event for event in events if event["event"] == "done")
        assert done["successful"] == 1, response.text
    else:
        assert response.json()["successful"] == 1, response.text
        assert response.json()["results"][0]["filename"] == "resume.txt"
    assert not any(path.is_file() for path in storage.rglob("*"))


def test_completed_lookup_uses_scanned_record_when_active_metadata_remains(upload_client, monkeypatch):
    client, storage = upload_client
    scans = []
    monkeypatch.setattr(upload, "validate_and_scan", lambda data, name: scans.append((data, name)))
    assert _post_chunk(client, filename="initial.pdf").status_code == 200
    active_path = storage / "safe-upload" / "metadata.json"
    active_metadata = active_path.read_bytes()
    response = client.post("/api/upload/finalize", json={
        "upload_id": "safe-upload", "filename": "scanned.txt", "total_chunks": 1,
    })
    assert response.status_code == 200
    # Recreate the actual pre-finalize record to represent failed chunk cleanup,
    # without mocking filesystem operations or the ownership implementation.
    active_path.parent.mkdir()
    active_path.write_bytes(active_metadata)
    path, filename = upload.get_assembled_upload("safe-upload", USER)
    assert filename == "scanned.txt"
    assert scans == [(path.read_bytes(), "scanned.txt")]


@pytest.mark.parametrize("completed_record", ["missing", "foreign", "corrupt"])
@pytest.mark.parametrize("operation", ["lookup", "remove", "finalize", "cancel", "chunk"])
def test_active_ownership_cannot_authorize_unowned_completed_bytes(
    upload_client, tmp_path, completed_record, operation,
):
    client, storage = upload_client
    assert _post_chunk(client).status_code == 200
    completed_dir = storage / ".assembled"
    completed_dir.mkdir()
    (completed_dir / "safe-upload.bin").write_bytes(b"previous tenant's completed bytes")
    if completed_record == "foreign":
        (completed_dir / "safe-upload.json").write_text(json.dumps({
            "tenant_id": 999, "filename": "foreign.pdf",
        }))
    elif completed_record == "corrupt":
        (completed_dir / "safe-upload.json").write_text("invalid json")
    before = _snapshot(tmp_path)
    if operation in {"lookup", "remove"}:
        action = upload.get_assembled_upload if operation == "lookup" else upload.remove_assembled_upload
        with pytest.raises(HTTPException) as error:
            action("safe-upload", USER)
        assert error.value.status_code == 404
    elif operation == "finalize":
        response = client.post("/api/upload/finalize", json={
            "upload_id": "safe-upload", "filename": "resume.pdf", "total_chunks": 1,
        })
        assert response.status_code == 404
    elif operation == "cancel":
        assert client.delete("/api/upload/cancel/safe-upload").status_code == 404
    else:
        assert _post_chunk(client, index=1).status_code == 404
    assert _snapshot(tmp_path) == before


def test_orphaned_completed_bytes_cannot_be_claimed_by_initial_chunk(upload_client, tmp_path):
    client, storage = upload_client
    completed_dir = storage / ".assembled"
    completed_dir.mkdir(parents=True)
    (completed_dir / "safe-upload.bin").write_bytes(b"previous tenant's completed bytes")
    before = _snapshot(tmp_path)
    response = _post_chunk(client)
    assert response.status_code == 404
    assert _snapshot(tmp_path) == before


def test_active_upload_can_still_be_cancelled_without_completed_artifacts(upload_client):
    client, storage = upload_client
    assert _post_chunk(client).status_code == 200
    assert client.delete("/api/upload/cancel/safe-upload").status_code == 200
    assert not any(path.is_file() for path in storage.rglob("*"))
