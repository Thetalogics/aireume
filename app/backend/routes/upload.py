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
import logging
import shutil
import tempfile
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

# Ensure chunk storage directory exists
CHUNK_STORAGE_DIR.mkdir(parents=True, exist_ok=True)


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


def _get_upload_dir(upload_id: str) -> Path:
    """Get the directory path for a specific upload ID."""
    return CHUNK_STORAGE_DIR / upload_id


def _get_chunk_path(upload_id: str, chunk_index: int) -> Path:
    """Get the file path for a specific chunk."""
    return _get_upload_dir(upload_id) / f"chunk_{chunk_index:04d}"


def _read_upload_metadata(upload_id: str) -> dict | None:
    meta_path = _get_upload_dir(upload_id) / "metadata.json"
    if not meta_path.exists():
        return None
    import json
    try:
        return json.loads(meta_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def assert_upload_owned(upload_id: str, current_user: User) -> dict:
    """Return upload metadata if it belongs to the current tenant; 404 otherwise."""
    safe_uid = upload_id.replace("..", "").replace("/", "").replace("\\", "")
    upload_dir = _get_upload_dir(safe_uid)
    if not upload_dir.exists():
        raise HTTPException(status_code=404, detail="Upload not found")
    meta = _read_upload_metadata(safe_uid)
    if not meta or int(meta.get("tenant_id") or -1) != int(current_user.tenant_id):
        raise HTTPException(status_code=404, detail="Upload not found")
    return meta


def _cleanup_old_chunks():
    """Remove chunk directories older than CHUNK_MAX_AGE_HOURS."""
    try:
        cutoff_time = datetime.now() - timedelta(hours=CHUNK_MAX_AGE_HOURS)
        for upload_dir in CHUNK_STORAGE_DIR.iterdir():
            if upload_dir.is_dir():
                dir_mtime = datetime.fromtimestamp(upload_dir.stat().st_mtime)
                if dir_mtime < cutoff_time:
                    shutil.rmtree(upload_dir, ignore_errors=True)
                    log.info(f"Cleaned up old chunk directory: {upload_dir.name}")
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
    metadata_path = upload_dir / "metadata.json"
    if metadata_path.exists():
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
        import json
        with open(metadata_path, 'w') as f:
            json.dump(metadata, f)
    
    # Write chunk to disk
    chunk_path = _get_chunk_path(upload_id, chunk_index)
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
    upload_id = request.upload_id
    assert_upload_owned(upload_id, current_user)
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
    assembled_dir = CHUNK_STORAGE_DIR / "assembled"
    assembled_dir.mkdir(parents=True, exist_ok=True)
    
    # Use upload_id in filename to avoid collisions
    safe_filename = f"{upload_id}_{request.filename}"
    assembled_path = assembled_dir / safe_filename
    
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
                validate_and_scan(f.read(), request.filename)
        except UnsafeFileError as e:
            assembled_path.unlink(missing_ok=True)
            log.warning("Assembled file failed validation: %s", e)
            raise HTTPException(status_code=422, detail="File failed security validation.") from e
        
        log.info(
            f"File assembled: upload_id={upload_id}, filename={request.filename}, "
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
            filename=request.filename,
            file_size=total_size,
            message=f"File assembled successfully: {request.filename} ({total_size} bytes)"
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
    assert_upload_owned(upload_id, current_user)
    upload_dir = _get_upload_dir(upload_id)
    try:
        shutil.rmtree(upload_dir, ignore_errors=True)
        log.info(f"Upload cancelled: upload_id={upload_id}, user={current_user.id}")
        return {"success": True, "message": f"Upload {upload_id} cancelled and cleaned up"}
    except OSError as e:
        log.error(
            "Failed to cancel upload %s: %s", upload_id, e,
            extra={"error_code": "IO_ERROR"},
        )
        raise HTTPException(status_code=502, detail="Upstream failure") from e
