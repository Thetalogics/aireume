"""
Chunked file upload routes for handling large file uploads that exceed CDN/proxy limits.

This module implements a robust chunked upload system that:
- Splits large files into smaller chunks (10MB each) on the client side
- Receives and stores chunks temporarily on the server
- Assembles chunks into complete files when all chunks are received
- Provides automatic cleanup of orphaned chunks after 24 hours
- Maintains backward compatibility with existing upload flows

Architecture:
1. Client splits file into chunks and uploads each chunk separately
2. Server stores chunks in /tmp/aria_chunks/{upload_id}/
3. Client calls finalize endpoint when all chunks uploaded
4. Server assembles chunks, validates integrity, and returns file path
5. Client uses assembled file for normal batch analysis flow

This solves the Cloudflare 100MB upload limit while keeping all traffic
behind Cloudflare's DDoS protection and rate limiting.
"""

import os
import hashlib
import json
import logging
import re
import shutil
import tempfile
import unicodedata
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException
from pydantic import BaseModel

from app.backend.middleware.auth import get_current_user, require_active_subscription
from app.backend.models.db_models import User
from app.backend.services.file_scan_service import validate_and_scan, UnsafeFileError

router = APIRouter(prefix="/api/upload", tags=["upload"])
log = logging.getLogger("aria.upload")

# Chunk storage configuration
CHUNK_STORAGE_DIR = Path(os.getenv("ARIA_CHUNK_STORAGE_DIR", str(Path(tempfile.gettempdir()) / "aria_chunks")))
CHUNK_MAX_AGE_HOURS = 24
MAX_CHUNK_SIZE = 15 * 1024 * 1024  # 15MB (with buffer for Cloudflare's 100MB limit)
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500MB total file size limit


class ChunkUploadResponse(BaseModel):
    """Response for chunk upload endpoint."""
    success: bool
    upload_id: str
    chunk_index: int
    total_chunks: int
    message: Optional[str] = None


class FinalizeUploadRequest(BaseModel):
    """Request body for finalize upload endpoint."""
    upload_id: str
    filename: str
    total_chunks: int
    file_hash: Optional[str] = None  # MD5 hash of original file for integrity check


class FinalizeUploadResponse(BaseModel):
    """Response for finalize upload endpoint."""
    success: bool
    upload_id: str
    filename: str
    file_size: int
    message: Optional[str] = None


def _validate_upload_id(upload_id: str) -> str:
    """Accept NanoIDs and UUID-shaped IDs without rewriting their identity."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", upload_id):
        raise HTTPException(status_code=400, detail="Invalid upload ID")
    return upload_id


def _safe_client_filename(filename: str) -> str:
    """Keep a bounded display basename; it is never a storage path."""
    if (
        not filename.strip()
        or len(filename) > 255
        or filename in {".", ".."}
        or any(char in filename for char in "/\\:")
        or any(unicodedata.category(char).startswith("C") for char in filename)
    ):
        raise HTTPException(status_code=400, detail="Invalid filename")
    return filename


def _contained_path(root: Path, *parts: str) -> Path:
    """Resolve symlinks and reject paths outside the intended storage root."""
    try:
        resolved_root = root.resolve()
        candidate = resolved_root.joinpath(*parts).resolve()
        candidate.relative_to(resolved_root)
        if parts and candidate == resolved_root:
            raise ValueError("Expected a child path")
        return candidate
    except (OSError, ValueError, RuntimeError) as error:
        raise HTTPException(status_code=400, detail="Invalid upload path") from error


def _get_upload_dir(upload_id: str) -> Path:
    return _contained_path(CHUNK_STORAGE_DIR, _validate_upload_id(upload_id))


def _get_chunk_path(upload_id: str, chunk_index: int) -> Path:
    """Get the file path for a specific chunk."""
    return _contained_path(_get_upload_dir(upload_id), f"chunk_{chunk_index:04d}")


def _get_assembled_path(upload_id: str) -> Path:
    upload_id = _validate_upload_id(upload_id)
    return _contained_path(_contained_path(CHUNK_STORAGE_DIR, ".assembled"), f"{upload_id}.bin")


def _get_metadata_path(upload_id: str, *, assembled: bool = False) -> Path:
    upload_id = _validate_upload_id(upload_id)
    if assembled:
        return _contained_path(_contained_path(CHUNK_STORAGE_DIR, ".assembled"), f"{upload_id}.json")
    return _contained_path(_get_upload_dir(upload_id), "metadata.json")


def _read_upload_metadata(upload_id: str, *, assembled: bool = False) -> dict | None:
    meta_path = _get_metadata_path(upload_id, assembled=assembled)
    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        return metadata if isinstance(metadata, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def assert_upload_owned(upload_id: str, current_user: User, *, assembled_only: bool = False) -> dict:
    """Return owner metadata, requiring the completed record for completed files."""
    upload_id = _validate_upload_id(upload_id)
    meta = None if assembled_only else _read_upload_metadata(upload_id)
    meta = meta or _read_upload_metadata(upload_id, assembled=True)
    if not meta or str(meta.get("tenant_id")) != str(current_user.tenant_id):
        raise HTTPException(status_code=404, detail="Upload not found")
    return meta


def get_assembled_upload(upload_id: str, current_user: User) -> tuple[Path, str]:
    """Locate completed bytes and their trusted display name for the owner."""
    metadata = assert_upload_owned(upload_id, current_user, assembled_only=True)
    filename = _safe_client_filename(metadata.get("filename") or "")
    path = _get_assembled_path(upload_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Upload not found")
    return path, filename


def remove_assembled_upload(upload_id: str, current_user: User) -> None:
    """Remove an owner's completed bytes and the matching ownership record."""
    assert_upload_owned(upload_id, current_user, assembled_only=True)
    path = _get_assembled_path(upload_id)
    metadata_path = _get_metadata_path(upload_id, assembled=True)
    path.unlink(missing_ok=True)
    metadata_path.unlink(missing_ok=True)


def _cleanup_old_chunks():
    """Expire inactive chunks and completed files after CHUNK_MAX_AGE_HOURS."""
    try:
        cutoff_time = datetime.now() - timedelta(hours=CHUNK_MAX_AGE_HOURS)
        for entry in _contained_path(CHUNK_STORAGE_DIR).iterdir():
            try:
                upload_dir = _get_upload_dir(entry.name)
            except HTTPException:
                continue
            if upload_dir.is_dir():
                dir_mtime = datetime.fromtimestamp(upload_dir.stat().st_mtime)
                if dir_mtime < cutoff_time:
                    shutil.rmtree(upload_dir, ignore_errors=True)
                    log.info(f"Cleaned up old chunk directory: {upload_dir.name}")
        assembled_dir = _contained_path(CHUNK_STORAGE_DIR, ".assembled")
        if assembled_dir.exists():
            for entry in assembled_dir.iterdir():
                if entry.suffix not in {".bin", ".json"}:
                    continue
                try:
                    upload_id = _validate_upload_id(entry.stem)
                    path = (_get_assembled_path(upload_id) if entry.suffix == ".bin"
                            else _get_metadata_path(upload_id, assembled=True))
                except HTTPException:
                    continue
                if path.is_file() and datetime.fromtimestamp(path.stat().st_mtime) < cutoff_time:
                    path.unlink()
    except OSError as e:
        log.warning(
            "Non-critical: Chunk cleanup failed: %s", e,
            extra={"error_code": "IO_ERROR"},
        )


@router.post("/chunk", response_model=ChunkUploadResponse)
async def upload_chunk(
    upload_id: str = Form(...),
    chunk_index: int = Form(...),
    total_chunks: int = Form(...),
    filename: str = Form(...),
    chunk: UploadFile = File(...),
    current_user: User = Depends(require_active_subscription),
):
    """
    Upload a single chunk of a file.
    
    This endpoint receives individual chunks of a large file and stores them
    temporarily until all chunks are received and can be assembled.
    
    Args:
        upload_id: Unique identifier for this upload session (UUID recommended)
        chunk_index: 0-based index of this chunk
        total_chunks: Total number of chunks for this file
        filename: Original filename (for validation and logging)
        chunk: Binary chunk data
        current_user: Authenticated user (from JWT)
    
    Returns:
        ChunkUploadResponse with success status and chunk info
    
    Raises:
        HTTPException: If chunk validation fails or storage error occurs
    """
    # Validate inputs
    upload_id = _validate_upload_id(upload_id)
    filename = _safe_client_filename(filename)
    if chunk_index < 0 or chunk_index >= total_chunks:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid chunk_index {chunk_index} for total_chunks {total_chunks}"
        )
    
    if total_chunks < 1 or total_chunks > 1000:
        raise HTTPException(
            status_code=400,
            detail="total_chunks must be between 1 and 1000"
        )
    
    # Read chunk data
    chunk_data = await chunk.read()
    chunk_size = len(chunk_data)
    
    # Validate chunk size
    if chunk_size > MAX_CHUNK_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Chunk size {chunk_size} bytes exceeds maximum {MAX_CHUNK_SIZE} bytes"
        )
    
    if chunk_size == 0:
        raise HTTPException(
            status_code=400,
            detail="Chunk data is empty"
        )

    upload_dir = _get_upload_dir(upload_id)
    metadata_path = _get_metadata_path(upload_id)
    assembled_path = _get_assembled_path(upload_id)
    assembled_metadata_path = _get_metadata_path(upload_id, assembled=True)
    chunk_path = _get_chunk_path(upload_id, chunk_index)
    if assembled_path.exists() or assembled_metadata_path.exists():
        assert_upload_owned(upload_id, current_user, assembled_only=True)
        raise HTTPException(status_code=409, detail="Upload already finalized")
    if upload_dir.exists():
        assert_upload_owned(upload_id, current_user)

    upload_dir.mkdir(parents=True, exist_ok=True)

    if not metadata_path.exists():
        metadata = {
            "upload_id": upload_id,
            "filename": filename,
            "total_chunks": total_chunks,
            "user_id": current_user.id,
            "tenant_id": current_user.tenant_id,
            "started_at": datetime.now().isoformat(),
        }
        with open(metadata_path, 'w', encoding="utf-8") as f:
            json.dump(metadata, f)
    
    # Write chunk to disk
    try:
        with open(chunk_path, 'wb') as f:
            f.write(chunk_data)
    except OSError as e:
        log.error(
            "Failed to write chunk %s for upload %s: %s", chunk_index, upload_id, e,
            extra={"error_code": "IO_ERROR"},
        )
        raise HTTPException(
            status_code=502,
            detail="Upstream failure",
        ) from e
    
    log.info(
        f"Chunk uploaded: upload_id={upload_id}, chunk={chunk_index}/{total_chunks}, "
        f"size={chunk_size}, user={current_user.id}"
    )
    
    # Cleanup old chunks periodically (every 10th chunk upload)
    if chunk_index % 10 == 0:
        _cleanup_old_chunks()
    
    return ChunkUploadResponse(
        success=True,
        upload_id=upload_id,
        chunk_index=chunk_index,
        total_chunks=total_chunks,
        message=f"Chunk {chunk_index + 1}/{total_chunks} uploaded successfully"
    )


@router.post("/finalize", response_model=FinalizeUploadResponse)
async def finalize_upload(
    request: FinalizeUploadRequest,
    current_user: User = Depends(require_active_subscription),
):
    """
    Finalize a chunked upload by assembling all chunks into a complete file.
    
    This endpoint:
    1. Validates all chunks are present
    2. Assembles chunks in order into a single file
    3. Optionally verifies file integrity via MD5 hash
    4. Stores the assembled file in a temporary location
    5. Returns the file path for use in batch analysis
    
    Args:
        request: FinalizeUploadRequest with upload_id, filename, total_chunks
        current_user: Authenticated user (from JWT)
    
    Returns:
        FinalizeUploadResponse with success status and file info
    
    Raises:
        HTTPException: If chunks are missing, assembly fails, or validation fails
    """
    upload_id = _validate_upload_id(request.upload_id)
    filename = _safe_client_filename(request.filename)
    metadata = assert_upload_owned(upload_id, current_user)
    upload_dir = _get_upload_dir(upload_id)
    
    # Verify upload directory exists
    if not upload_dir.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Upload {upload_id} not found. Chunks may have expired."
        )
    
    # Verify all chunks are present
    missing_chunks = []
    for i in range(request.total_chunks):
        chunk_path = _get_chunk_path(upload_id, i)
        if not chunk_path.exists():
            missing_chunks.append(i)
    
    if missing_chunks:
        raise HTTPException(
            status_code=400,
            detail=f"Missing chunks: {missing_chunks}. Please re-upload missing chunks."
        )
    
    # Assemble chunks into final file
    assembled_path = _get_assembled_path(upload_id)
    assembled_metadata_path = _get_metadata_path(upload_id, assembled=True)
    if assembled_path.exists() or assembled_metadata_path.exists():
        assert_upload_owned(upload_id, current_user, assembled_only=True)
        raise HTTPException(status_code=409, detail="Upload already finalized")
    assembled_path.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        total_size = 0
        md5_hash = hashlib.md5()
        
        with open(assembled_path, 'wb') as outfile:
            for i in range(request.total_chunks):
                chunk_path = _get_chunk_path(upload_id, i)
                with open(chunk_path, 'rb') as infile:
                    chunk_data = infile.read()
                    outfile.write(chunk_data)
                    total_size += len(chunk_data)
                    md5_hash.update(chunk_data)
        
        # Validate total file size
        if total_size > MAX_FILE_SIZE:
            assembled_path.unlink()  # Delete assembled file
            raise HTTPException(
                status_code=400,
                detail=f"Assembled file size {total_size} bytes exceeds maximum {MAX_FILE_SIZE} bytes"
            )
        
        # Verify file hash if provided
        if request.file_hash:
            computed_hash = md5_hash.hexdigest()
            if computed_hash != request.file_hash:
                assembled_path.unlink()  # Delete corrupted file
                raise HTTPException(
                    status_code=400,
                    detail="File integrity check failed. MD5 hash mismatch. Please re-upload."
                )

        # Content validation + optional malware scan before the file is usable.
        try:
            with open(assembled_path, 'rb') as f:
                validate_and_scan(f.read(), filename)
        except UnsafeFileError as e:
            assembled_path.unlink(missing_ok=True)
            log.warning("Assembled file failed validation: %s", e)
            raise HTTPException(status_code=422, detail="File failed security validation.") from e
        
        # Keep tenant ownership and the scanned display name after chunk cleanup.
        metadata["filename"] = filename
        assembled_metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

        log.info(
            f"File assembled: upload_id={upload_id}, filename={filename}, "
            f"size={total_size}, chunks={request.total_chunks}, user={current_user.id}"
        )
        
        # Clean up chunk directory after successful assembly
        try:
            shutil.rmtree(upload_dir, ignore_errors=True)
        except OSError as e:
            log.warning(
                "Non-critical: Failed to cleanup chunks for %s: %s", upload_id, e,
                extra={"error_code": "IO_ERROR"},
            )
        
        return FinalizeUploadResponse(
            success=True,
            upload_id=upload_id,
            filename=filename,
            file_size=total_size,
            message=f"File assembled successfully: {filename} ({total_size} bytes)"
        )
        
    except HTTPException:
        raise
    except (ValueError, TypeError, KeyError) as e:
        log.warning(
            "Failed to assemble file for upload %s: %s", upload_id, e,
            extra={"error_code": "VALIDATION_ERROR"},
        )
        if assembled_path.exists():
            assembled_path.unlink()
        raise HTTPException(status_code=400, detail="Invalid request") from e
    except OSError as e:
        log.error(
            "Failed to assemble file for upload %s: %s", upload_id, e,
            extra={"error_code": "IO_ERROR"},
        )
        if assembled_path.exists():
            assembled_path.unlink()
        raise HTTPException(status_code=502, detail="Upstream failure") from e


@router.delete("/cancel/{upload_id}")
async def cancel_upload(
    upload_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Cancel an in-progress chunked upload and clean up stored chunks.
    
    This endpoint allows clients to abort an upload and free up storage space.
    
    Args:
        upload_id: Unique identifier for the upload to cancel
        current_user: Authenticated user (from JWT)
    
    Returns:
        Success message
    """
    upload_id = _validate_upload_id(upload_id)
    assert_upload_owned(upload_id, current_user)
    upload_dir = _get_upload_dir(upload_id)
    try:
        if _get_assembled_path(upload_id).exists() or _get_metadata_path(upload_id, assembled=True).exists():
            remove_assembled_upload(upload_id, current_user)
        shutil.rmtree(upload_dir, ignore_errors=True)
        log.info(f"Upload cancelled: upload_id={upload_id}, user={current_user.id}")
        return {"success": True, "message": f"Upload {upload_id} cancelled and cleaned up"}
    except OSError as e:
        log.error(
            "Failed to cancel upload %s: %s", upload_id, e,
            extra={"error_code": "IO_ERROR"},
        )
        raise HTTPException(status_code=502, detail="Upstream failure") from e
