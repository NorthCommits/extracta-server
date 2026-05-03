import logging
import os
import shutil
import uuid
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from config import MAX_FILE_SIZE_BYTES, SUPPORTED_FORMATS, TEMP_DIR
from models.schemas import ErrorResponse, ExtractResponse
from pipeline.orchestrator import run_pipeline

logger = logging.getLogger("extracta.router.extract")

router = APIRouter()


def _ensure_temp_dir():
    """Create temp directory if it does not exist."""
    os.makedirs(TEMP_DIR, exist_ok=True)


def _get_file_extension(filename: str) -> str:
    """Extract and normalise file extension."""
    ext = os.path.splitext(filename)[-1].lower().lstrip(".")
    if ext == "htm":
        return "html"
    return ext


def _save_upload(upload_file: UploadFile) -> tuple[str, str]:
    """
    Save uploaded file to temp directory with a unique name.
    Returns (temp_file_path, original_filename).
    """
    _ensure_temp_dir()

    original_name = upload_file.filename or "unknown"
    ext = _get_file_extension(original_name)
    unique_name = f"{uuid.uuid4().hex}.{ext}"
    temp_path = os.path.join(TEMP_DIR, unique_name)

    with open(temp_path, "wb") as f:
        shutil.copyfileobj(upload_file.file, f)

    file_size = os.path.getsize(temp_path)
    logger.info(
        f"[Router] File saved -- "
        f"original={original_name} "
        f"temp={temp_path} "
        f"size={file_size / 1024:.1f} KB"
    )

    return temp_path, original_name


def _cleanup(temp_path: str):
    """Remove temp file after processing."""
    try:
        if os.path.exists(temp_path):
            os.remove(temp_path)
            logger.info(f"[Router] Temp file cleaned up: {temp_path}")
    except Exception as e:
        logger.warning(f"[Router] Could not clean up temp file {temp_path}: {e}")


@router.post(
    "/extract",
    response_model=ExtractResponse,
    responses={
        400: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    summary="Extract document with agentic context threading",
    description=(
        "Upload a PDF, PPTX, DOCX, or HTML file. "
        "Extracta will detect layout per page, determine reading order, "
        "extract blocks, and run ADE context threading via LLM. "
        "Returns structured JSON with full context metadata."
    ),
)
async def extract_document(
    file: Annotated[UploadFile, File(description="Document to extract (PDF, PPTX, DOCX, HTML)")]
):
    temp_path = None

    try:
        # Validate filename
        if not file.filename:
            logger.warning("[Router] Upload rejected -- no filename provided")
            raise HTTPException(
                status_code=400,
                detail="No filename provided in upload."
            )

        ext = _get_file_extension(file.filename)

        # Validate format
        if ext not in SUPPORTED_FORMATS:
            logger.warning(
                f"[Router] Upload rejected -- unsupported format: .{ext} "
                f"| file={file.filename}"
            )
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unsupported file format: .{ext}. "
                    f"Supported formats: {', '.join(SUPPORTED_FORMATS)}"
                )
            )

        # Validate file size
        file.file.seek(0, 2)  # Seek to end
        file_size = file.file.tell()
        file.file.seek(0)  # Reset

        if file_size > MAX_FILE_SIZE_BYTES:
            logger.warning(
                f"[Router] Upload rejected -- file too large: "
                f"{file_size / (1024 * 1024):.1f} MB > "
                f"{MAX_FILE_SIZE_BYTES / (1024 * 1024):.0f} MB limit "
                f"| file={file.filename}"
            )
            raise HTTPException(
                status_code=400,
                detail=(
                    f"File too large: {file_size / (1024 * 1024):.1f} MB. "
                    f"Maximum allowed: {MAX_FILE_SIZE_BYTES / (1024 * 1024):.0f} MB."
                )
            )

        logger.info(
            f"[Router] Accepted upload -- "
            f"file={file.filename} "
            f"format={ext} "
            f"size={file_size / 1024:.1f} KB"
        )

        # Save to temp
        temp_path, original_name = _save_upload(file)

        # Run pipeline
        result = run_pipeline(
            file_path=temp_path,
            file_name=original_name,
        )

        logger.info(
            f"[Router] Response ready -- "
            f"file={original_name} "
            f"pages={result.total_pages} "
            f"regions={sum(len(p.regions) for p in result.pages)}"
        )

        return result

    except HTTPException:
        raise

    except ValueError as e:
        logger.error(f"[Router] Validation error: {e}")
        raise HTTPException(status_code=422, detail=str(e))

    except Exception as e:
        logger.error(f"[Router] Unexpected error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error during extraction: {str(e)}"
        )

    finally:
        if temp_path:
            _cleanup(temp_path)