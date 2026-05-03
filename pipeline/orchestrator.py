import logging
import os
import time
from typing import Dict

from parsers.pdf_parser import PDFParser
from parsers.pptx_parser import PPTXParser
from parsers.docx_parser import DOCXParser
from parsers.html_parser import HTMLParser
from pipeline.detector import detect_all_pages
from pipeline.extractor import extract_all_pages
from pipeline.ade_agent import run_ade
from models.schemas import (
    ExtractResponse,
    PageResult,
    RegionBlock,
    BoundingBox,
)
from config import SUPPORTED_FORMATS

logger = logging.getLogger("extracta.pipeline.orchestrator")


def _get_parser(file_path: str, file_format: str):
    """Return the correct parser instance for the given format."""
    parsers = {
        "pdf": PDFParser,
        "pptx": PPTXParser,
        "docx": DOCXParser,
        "html": HTMLParser,
    }
    cls = parsers.get(file_format)
    if not cls:
        raise ValueError(f"Unsupported format: {file_format}")
    return cls(file_path)


def _detect_format(file_path: str) -> str:
    """Detect file format from extension."""
    ext = os.path.splitext(file_path)[-1].lower().lstrip(".")
    if ext == "htm":
        return "html"
    if ext not in SUPPORTED_FORMATS:
        raise ValueError(
            f"Unsupported file extension: .{ext} | "
            f"Supported: {SUPPORTED_FORMATS}"
        )
    return ext


def _build_response(
    file_name: str,
    file_format: str,
    pages: list,
) -> ExtractResponse:
    """
    Convert enriched pipeline pages into the final ExtractResponse schema.
    """
    page_results = []

    for page in pages:
        page_number = page["page_number"]
        layout_type = page.get("layout_type", "unknown")
        strategy = page.get("strategy", "v_major")
        ordered_blocks = page.get("ordered_blocks", [])
        full_text = page.get("full_text", "")

        region_blocks = []

        for block in ordered_blocks:
            raw_bbox = block.get("bbox", (0, 0, 0, 0))

            # Normalize bbox -- handle both tuple and dict formats
            if isinstance(raw_bbox, (list, tuple)) and len(raw_bbox) == 4:
                bbox = BoundingBox(
                    x0=raw_bbox[0],
                    y0=raw_bbox[1],
                    x1=raw_bbox[2],
                    y1=raw_bbox[3],
                )
            else:
                bbox = BoundingBox(x0=0, y0=0, x1=0, y1=0)

            # Normalize block_type to schema literal
            raw_type = block.get("block_type", "unknown")
            block_type = _normalize_block_type(raw_type)

            # Normalize context_role to schema literal
            raw_role = block.get("context_role")
            context_role = _normalize_context_role(raw_role)

            region_blocks.append(RegionBlock(
                region_id=block.get("region_id", f"p{page_number}_r{block.get('sequence', 0)}"),
                type=block_type,
                text=block.get("text", ""),
                bbox=bbox,
                sequence=block.get("sequence", 0),
                context_thread_id=block.get("context_thread_id"),
                context_role=context_role,
                continues_on_page=block.get("continues_on_page"),
                references_region=block.get("references_region"),
            ))

        page_results.append(PageResult(
            page_number=page_number,
            layout_type=layout_type,
            strategy=strategy,
            regions=region_blocks,
            full_text=full_text,
        ))

    return ExtractResponse(
        file=file_name,
        format=file_format,
        total_pages=len(page_results),
        pages=page_results,
    )


def _normalize_block_type(raw: str) -> str:
    """Map raw parser block types to schema literals."""
    mapping = {
        "title": "title",
        "heading": "heading",
        "body": "body",
        "text": "body",
        "table": "table",
        "image": "image",
        "caption": "caption",
        "footer": "footer",
        "header": "header",
        "sidebar": "sidebar",
        "callout": "callout",
    }
    return mapping.get(raw, "unknown")


def _normalize_context_role(raw: str) -> str:
    """Map raw ADE context roles to schema literals."""
    valid = {"heading", "body", "callout", "caption", "footnote", "continuation"}
    if raw in valid:
        return raw
    return None


def run_pipeline(file_path: str, file_name: str) -> ExtractResponse:
    """
    Main pipeline orchestrator.

    Steps:
    1. Detect file format
    2. Parse document into raw pages with blocks
    3. Detect layout per page (H-major / V-major + layout_type)
    4. Extract blocks in natural reading order using XY-Cut
    5. Run ADE agent for context threading
    6. Build and return structured ExtractResponse

    All steps are timed and logged.
    """
    pipeline_start = time.time()
    logger.info(
        f"[Orchestrator] Pipeline started -- "
        f"file={file_name}"
    )

    # Step 1 -- Format detection
    try:
        file_format = _detect_format(file_path)
        logger.info(f"[Orchestrator] Detected format: {file_format}")
    except ValueError as e:
        logger.error(f"[Orchestrator] Format detection failed: {e}")
        raise

    # Step 2 -- Parsing
    t0 = time.time()
    logger.info(f"[Orchestrator] Step 2 -- Parsing with {file_format.upper()}Parser")
    try:
        parser = _get_parser(file_path, file_format)
        raw_pages = parser.extract_pages()
        logger.info(
            f"[Orchestrator] Parsing complete -- "
            f"{len(raw_pages)} pages in {time.time() - t0:.2f}s"
        )
    except Exception as e:
        logger.error(f"[Orchestrator] Parsing failed: {e}", exc_info=True)
        raise

    # Step 3 -- Layout detection
    t0 = time.time()
    logger.info(f"[Orchestrator] Step 3 -- Layout detection")
    try:
        detected_pages = detect_all_pages(raw_pages)
        logger.info(
            f"[Orchestrator] Detection complete -- "
            f"{len(detected_pages)} pages in {time.time() - t0:.2f}s"
        )
    except Exception as e:
        logger.error(f"[Orchestrator] Detection failed: {e}", exc_info=True)
        raise

    # Step 4 -- Extraction
    t0 = time.time()
    logger.info(f"[Orchestrator] Step 4 -- Reading order extraction")
    try:
        extracted_pages = extract_all_pages(detected_pages)
        logger.info(
            f"[Orchestrator] Extraction complete -- "
            f"{len(extracted_pages)} pages in {time.time() - t0:.2f}s"
        )
    except Exception as e:
        logger.error(f"[Orchestrator] Extraction failed: {e}", exc_info=True)
        raise

    # Step 5 -- ADE context threading
    t0 = time.time()
    logger.info(f"[Orchestrator] Step 5 -- ADE context threading")
    try:
        ade_pages = run_ade(extracted_pages)
        logger.info(
            f"[Orchestrator] ADE complete -- "
            f"{len(ade_pages)} pages in {time.time() - t0:.2f}s"
        )
    except Exception as e:
        logger.error(f"[Orchestrator] ADE failed: {e}", exc_info=True)
        raise

    # Step 6 -- Build response
    logger.info(f"[Orchestrator] Step 6 -- Building response")
    response = _build_response(file_name, file_format, ade_pages)

    total_time = time.time() - pipeline_start
    total_blocks = sum(len(p.regions) for p in response.pages)

    logger.info(
        f"[Orchestrator] Pipeline complete -- "
        f"pages={response.total_pages} "
        f"blocks={total_blocks} "
        f"total_time={total_time:.2f}s"
    )

    return response