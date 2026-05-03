import logging
from typing import List, Dict

from algorithms.xy_cut import xy_cut, flatten_regions_to_blocks
from utils.bbox_utils import get_page_dimensions

logger = logging.getLogger("extracta.pipeline.extractor")


def extract_page(page: Dict) -> Dict:
    """
    Apply XY-Cut segmentation to a single detected page using
    the strategy determined by the detector.

    Input: enriched page dict with layout_type and strategy fields.

    Output: page dict with ordered_blocks added -- blocks in
    natural human reading order based on the detected strategy.
    """
    page_number = page["page_number"]
    page_width = page["page_width"]
    page_height = page["page_height"]
    blocks = page["blocks"]
    strategy = page.get("strategy", "v_major")
    layout_type = page.get("layout_type", "single_col")

    logger.info(
        f"[Extractor] Page {page_number} -- "
        f"strategy={strategy} layout_type={layout_type} "
        f"blocks={len(blocks)}"
    )

    if not blocks:
        logger.warning(f"[Extractor] Page {page_number} -- no blocks to extract")
        return {
            **page,
            "ordered_blocks": [],
            "full_text": "",
        }

    # For simple single column layouts, skip XY-Cut overhead
    # and just sort by natural reading order directly
    if layout_type == "single_col":
        logger.info(f"[Extractor] Page {page_number} -- single_col, using direct sort")
        ordered = _sort_single_column(blocks)
        return {
            **page,
            "ordered_blocks": _assign_sequence(ordered),
            "full_text": _build_full_text(ordered),
        }

    # For table-heavy pages, preserve table block order
    if layout_type == "table_heavy":
        logger.info(f"[Extractor] Page {page_number} -- table_heavy, using top-down sort")
        ordered = _sort_single_column(blocks)
        return {
            **page,
            "ordered_blocks": _assign_sequence(ordered),
            "full_text": _build_full_text(ordered),
        }

    # For all other layouts -- run full XY-Cut segmentation
    logger.info(
        f"[Extractor] Page {page_number} -- "
        f"running XY-Cut (strategy={strategy})"
    )

    try:
        regions = xy_cut(
            blocks=blocks,
            x0=0.0,
            y0=0.0,
            x1=page_width,
            y1=page_height,
            strategy=strategy,
            depth=0,
            max_depth=6,
        )

        logger.info(
            f"[Extractor] Page {page_number} -- "
            f"XY-Cut produced {len(regions)} regions"
        )

        ordered = flatten_regions_to_blocks(regions, strategy=strategy)

    except Exception as e:
        logger.error(
            f"[Extractor] Page {page_number} -- XY-Cut failed: {e}",
            exc_info=True
        )
        # Fallback to simple sort on failure
        logger.warning(f"[Extractor] Page {page_number} -- falling back to direct sort")
        ordered = _sort_single_column(blocks)

    ordered = _assign_sequence(ordered)
    full_text = _build_full_text(ordered)

    logger.info(
        f"[Extractor] Page {page_number} -- "
        f"extraction complete | {len(ordered)} ordered blocks"
    )

    return {
        **page,
        "ordered_blocks": ordered,
        "full_text": full_text,
    }


def extract_all_pages(pages: List[Dict]) -> List[Dict]:
    """
    Run extraction across all detected pages.
    Returns pages with ordered_blocks and full_text added.
    """
    logger.info(f"[Extractor] Starting extraction for {len(pages)} pages")
    extracted = []

    for page in pages:
        try:
            extracted_page = extract_page(page)
            extracted.append(extracted_page)
        except Exception as e:
            logger.error(
                f"[Extractor] Failed on page {page.get('page_number', '?')}: {e}",
                exc_info=True
            )
            extracted.append({
                **page,
                "ordered_blocks": [],
                "full_text": "",
            })

    total_blocks = sum(len(p.get("ordered_blocks", [])) for p in extracted)
    logger.info(
        f"[Extractor] Extraction complete -- "
        f"{len(extracted)} pages | {total_blocks} total ordered blocks"
    )

    return extracted


def _sort_single_column(blocks: List[dict]) -> List[dict]:
    """
    Sort blocks top-to-bottom, left-to-right.
    Used for single column and table-heavy pages.
    """
    return sorted(blocks, key=lambda b: (b["bbox"][1], b["bbox"][0]))


def _assign_sequence(blocks: List[dict]) -> List[dict]:
    """
    Assign a sequence number to each block reflecting
    its position in natural reading order.
    """
    sequenced = []
    for idx, block in enumerate(blocks):
        b = dict(block)
        b["sequence"] = idx + 1
        sequenced.append(b)
    return sequenced


def _build_full_text(blocks: List[dict]) -> str:
    """
    Join all block texts in reading order into a single
    coherent full-text string for the page.
    Preserves paragraph breaks between blocks.
    """
    parts = []
    for block in blocks:
        text = block.get("text", "").strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)