import logging
from typing import List, Dict

from algorithms.projection import (
    build_vertical_projection,
    build_horizontal_projection,
    dominant_axis,
)
from algorithms.manhattan import classify_layout

logger = logging.getLogger("extracta.pipeline.detector")


def detect_page_layout(page: Dict) -> Dict:
    """
    Analyse a single page/slide and determine:
    - layout_type: single_col, multi_col, mixed, table_heavy, image_heavy
    - strategy: h_major or v_major
    - supporting metrics from projection and manhattan analysis

    Input page dict:
    {
        "page_number": int,
        "page_width": float,
        "page_height": float,
        "blocks": List[dict]
    }

    Returns enriched page dict with layout metadata added.
    """
    page_number = page["page_number"]
    page_width = page["page_width"]
    page_height = page["page_height"]
    blocks = page["blocks"]

    logger.info(
        f"[Detector] Analysing page {page_number} -- "
        f"{len(blocks)} blocks | {page_width:.1f}x{page_height:.1f} pt"
    )

    if not blocks:
        logger.warning(f"[Detector] Page {page_number} has no blocks -- defaulting to single_col / h_major")
        return {
            **page,
            "layout_type": "single_col",
            "strategy": "h_major",
            "detection_meta": {}
        }

    # Step 1 -- Manhattan vs Non-Manhattan classification
    manhattan_result = classify_layout(blocks, page_width, page_height)
    logger.info(
        f"[Detector] Page {page_number} -- Manhattan={manhattan_result['is_manhattan']} "
        f"confidence={manhattan_result['confidence']:.2f} "
        f"layout_type_hint={manhattan_result['layout_type']} "
        f"overlaps={manhattan_result['overlap_count']}"
    )

    # Step 2 -- Build projection profiles
    v_profile = build_vertical_projection(blocks, page_width)
    h_profile = build_horizontal_projection(blocks, page_height)

    logger.debug(
        f"[Detector] Page {page_number} -- "
        f"V-profile buckets={len(v_profile)} | H-profile buckets={len(h_profile)}"
    )

    # Step 3 -- Determine dominant axis (H-major vs V-major)
    axis_result = dominant_axis(v_profile, h_profile, page_width, page_height)
    logger.info(
        f"[Detector] Page {page_number} -- "
        f"Strategy={axis_result['strategy']} | "
        f"V-score={axis_result['v_score']:.2f} H-score={axis_result['h_score']:.2f} | "
        f"Est. columns={axis_result['num_columns_estimate']} "
        f"Est. rows={axis_result['num_rows_estimate']}"
    )

    # Step 4 -- Resolve layout_type
    # Manhattan gives a layout_type hint, but we refine using projection data
    layout_type = _resolve_layout_type(
        manhattan_result=manhattan_result,
        axis_result=axis_result,
        blocks=blocks,
        page_width=page_width,
        page_height=page_height,
    )
    logger.info(f"[Detector] Page {page_number} -- Final layout_type={layout_type}")

    detection_meta = {
        "manhattan": manhattan_result,
        "axis": axis_result,
    }

    return {
        **page,
        "layout_type": layout_type,
        "strategy": axis_result["strategy"],
        "detection_meta": detection_meta,
    }


def detect_all_pages(pages: List[Dict]) -> List[Dict]:
    """
    Run layout detection across all pages of a document.
    Returns enriched pages list with layout_type and strategy per page.
    """
    logger.info(f"[Detector] Starting layout detection for {len(pages)} pages")
    enriched = []

    for page in pages:
        try:
            enriched_page = detect_page_layout(page)
            enriched.append(enriched_page)
        except Exception as e:
            logger.error(
                f"[Detector] Failed on page {page.get('page_number', '?')}: {e}",
                exc_info=True
            )
            # Fallback -- don't crash the pipeline
            enriched.append({
                **page,
                "layout_type": "unknown",
                "strategy": "v_major",
                "detection_meta": {"error": str(e)},
            })

    strategy_summary = {}
    for p in enriched:
        s = p.get("strategy", "unknown")
        strategy_summary[s] = strategy_summary.get(s, 0) + 1

    logger.info(
        f"[Detector] Detection complete -- "
        f"strategy distribution: {strategy_summary}"
    )

    return enriched


def _resolve_layout_type(
    manhattan_result: Dict,
    axis_result: Dict,
    blocks: List[dict],
    page_width: float,
    page_height: float,
) -> str:
    """
    Final layout type resolution combining Manhattan classification
    and projection axis analysis.

    Priority:
    1. If majority blocks are images -- image_heavy
    2. If Manhattan table_heavy hint -- table_heavy
    3. If Non-Manhattan -- mixed
    4. If V-major with multiple columns -- multi_col
    5. Default -- single_col
    """
    if not blocks:
        return "single_col"

    # Check image density
    image_blocks = [b for b in blocks if b.get("block_type") == "image"]
    image_ratio = len(image_blocks) / len(blocks)
    if image_ratio > 0.5:
        logger.debug(f"[Detector] image_ratio={image_ratio:.2f} -- classifying as image_heavy")
        return "image_heavy"

    # Check table density
    table_blocks = [b for b in blocks if b.get("block_type") == "table"]
    table_ratio = len(table_blocks) / len(blocks)
    if manhattan_result["layout_type"] == "table_heavy" or table_ratio > 0.4:
        logger.debug(f"[Detector] table_ratio={table_ratio:.2f} -- classifying as table_heavy")
        return "table_heavy"

    # Non-Manhattan = mixed layout
    if not manhattan_result["is_manhattan"]:
        return "mixed"

    # V-major with multiple estimated columns = multi_col
    if (
        axis_result["strategy"] == "v_major"
        and axis_result["num_columns_estimate"] >= 2
    ):
        return "multi_col"

    return "single_col"