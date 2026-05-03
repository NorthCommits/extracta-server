import json
import logging
from typing import List, Dict, Optional
from openai import OpenAI
from config import OPENAI_API_KEY, OPENAI_MODEL

logger = logging.getLogger("extracta.pipeline.ade_agent")

client = OpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT = """
You are an expert document analyst specialising in agentic document extraction (ADE).

You will receive a structured list of text regions extracted from a document.
Each region has: region_id, page_number, sequence, block_type, text, and bbox.

Your job is to analyse these regions and assign context threading metadata to each one.

For every region you must assign:

1. context_thread_id (string):
   - A thread ID like "thread_001", "thread_002" etc.
   - Regions that belong to the same logical narrative or argument share a thread ID.
   - A title/heading and the body text beneath it = same thread.
   - A callout/sidebar that references body text = its own thread, but note the reference.
   - Unrelated sections = different threads.

2. context_role (string), one of:
   - "heading"      -- section title or heading
   - "body"         -- main body paragraph
   - "callout"      -- sidebar, highlighted box, callout
   - "caption"      -- image or table caption
   - "footnote"     -- footer or footnote text
   - "continuation" -- continues directly from a previous block (e.g. text split across columns or pages)

3. continues_on_page (int or null):
   - If this block's text is clearly cut off mid-sentence and continues on the next page, set this to the next page number.
   - Otherwise null.

4. references_region (string or null):
   - If this block is a callout or caption that directly references another region, set this to that region's region_id.
   - Otherwise null.

Rules:
- Be conservative -- only set continues_on_page if you are confident text is cut off.
- Be precise with thread IDs -- do not group unrelated content into the same thread.
- Headings always start a new thread unless they are clearly a sub-heading of the current thread.
- Tables and images are their own thread unless they have a caption, in which case caption joins the table/image thread.
- Return ONLY a valid JSON array of objects. No explanation, no markdown, no preamble, no code fences.
- Every element in the array MUST be a JSON object (not a number, string, or null).
- Each object in the array must have exactly these keys: region_id, context_thread_id, context_role, continues_on_page, references_region.

Example output:
[
  {
    "region_id": "p1_r1",
    "context_thread_id": "thread_001",
    "context_role": "heading",
    "continues_on_page": null,
    "references_region": null
  },
  {
    "region_id": "p1_r2",
    "context_thread_id": "thread_001",
    "context_role": "body",
    "continues_on_page": null,
    "references_region": null
  }
]
"""


def _build_regions_payload(pages: List[Dict]) -> List[Dict]:
    """
    Flatten all ordered blocks across all pages into a single
    list of region dicts for the LLM prompt.
    Strips heavy data (raw detection meta) to keep token count low.
    """
    regions = []

    for page in pages:
        page_number = page["page_number"]
        ordered_blocks = page.get("ordered_blocks", [])

        for block in ordered_blocks:
            bbox = block.get("bbox", (0, 0, 0, 0))
            regions.append({
                "region_id": f"p{page_number}_r{block.get('sequence', 0)}",
                "page_number": page_number,
                "sequence": block.get("sequence", 0),
                "block_type": block.get("block_type", "text"),
                "text": block.get("text", "")[:500],  # cap per block to control tokens
                "bbox": {
                    "x0": round(bbox[0], 1),
                    "y0": round(bbox[1], 1),
                    "x1": round(bbox[2], 1),
                    "y1": round(bbox[3], 1),
                },
            })

    logger.info(f"[ADEAgent] Built payload with {len(regions)} regions across {len(pages)} pages")
    return regions


def _parse_llm_response(raw: str) -> Optional[List[Dict]]:
    """
    Robustly parse LLM response into a list of region dicts.
    Handles markdown fences, wrapped dicts, and filters non-dict elements.
    """
    if not raw:
        return None

    # Strip markdown code fences
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        # Remove first and last fence lines
        lines = [l for l in lines if not l.strip().startswith("```")]
        raw = "\n".join(lines).strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"[ADEAgent] JSON parse error: {e}")
        logger.debug(f"[ADEAgent] Raw response was: {raw[:1000]}")
        return None

    # Handle direct list
    if isinstance(parsed, list):
        # Filter to only dict elements with region_id
        result = [r for r in parsed if isinstance(r, dict) and "region_id" in r]
        if len(result) != len(parsed):
            logger.warning(
                f"[ADEAgent] Filtered out {len(parsed) - len(result)} "
                f"non-dict elements from LLM response"
            )
        return result if result else None

    # Handle wrapped dict responses
    if isinstance(parsed, dict):
        for key in ["regions", "results", "data", "output", "items"]:
            if key in parsed and isinstance(parsed[key], list):
                result = [r for r in parsed[key] if isinstance(r, dict) and "region_id" in r]
                return result if result else None

        # Dict keyed by region_id or index -- flatten values
        values = list(parsed.values())
        if values and isinstance(values[0], dict) and "region_id" in values[0]:
            return values

    logger.error(f"[ADEAgent] Unexpected response structure: {type(parsed)}")
    return None


def _call_llm(regions_payload: List[Dict]) -> Optional[List[Dict]]:
    """
    Call OpenAI with the regions payload.
    Returns parsed JSON list of context threading results.
    """
    user_message = (
        "Here are the extracted document regions in reading order.\n"
        "Analyse them and return the context threading metadata as a JSON array.\n"
        "Return ONLY the JSON array. No markdown, no explanation, no code fences.\n\n"
        f"{json.dumps(regions_payload, indent=2)}"
    )

    logger.info(f"[ADEAgent] Sending {len(regions_payload)} regions to {OPENAI_MODEL}")

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.0,
        )

        raw = response.choices[0].message.content
        logger.info(
            f"[ADEAgent] LLM response received -- "
            f"tokens used: {response.usage.total_tokens}"
        )
        logger.debug(f"[ADEAgent] Raw LLM response: {raw[:500]}...")

        result = _parse_llm_response(raw)

        if result is None:
            logger.error("[ADEAgent] Could not parse LLM response into valid region list")
            return None

        logger.info(f"[ADEAgent] Successfully parsed {len(result)} region results")
        return result

    except Exception as e:
        logger.error(f"[ADEAgent] LLM call failed: {e}", exc_info=True)
        return None


def _merge_ade_results(pages: List[Dict], ade_results: List[Dict]) -> List[Dict]:
    """
    Merge LLM context threading results back into the extracted pages.
    Matches by region_id.
    """
    # Build lookup map -- only include valid dict entries with region_id
    ade_map = {
        r["region_id"]: r
        for r in ade_results
        if isinstance(r, dict) and "region_id" in r
    }

    logger.info(f"[ADEAgent] Merging {len(ade_map)} ADE results into pages")

    merged_pages = []
    matched = 0
    unmatched = 0

    for page in pages:
        page_number = page["page_number"]
        ordered_blocks = page.get("ordered_blocks", [])
        merged_blocks = []

        for block in ordered_blocks:
            region_id = f"p{page_number}_r{block.get('sequence', 0)}"
            ade = ade_map.get(region_id)

            if ade:
                matched += 1
                block = {
                    **block,
                    "region_id": region_id,
                    "context_thread_id": ade.get("context_thread_id"),
                    "context_role": ade.get("context_role"),
                    "continues_on_page": ade.get("continues_on_page"),
                    "references_region": ade.get("references_region"),
                }
            else:
                unmatched += 1
                logger.debug(f"[ADEAgent] No ADE result for region {region_id}")
                block = {
                    **block,
                    "region_id": region_id,
                    "context_thread_id": None,
                    "context_role": None,
                    "continues_on_page": None,
                    "references_region": None,
                }

            merged_blocks.append(block)

        merged_pages.append({
            **page,
            "ordered_blocks": merged_blocks,
        })

    logger.info(
        f"[ADEAgent] Merge complete -- "
        f"matched={matched} unmatched={unmatched}"
    )

    return merged_pages


def _batch_regions(regions: List[Dict], batch_size: int = 80) -> List[List[Dict]]:
    """
    Split regions into batches to avoid token limits.
    Each batch is processed independently then merged.
    """
    batches = []
    for i in range(0, len(regions), batch_size):
        batches.append(regions[i:i + batch_size])
    logger.info(
        f"[ADEAgent] Split {len(regions)} regions into "
        f"{len(batches)} batch(es) of max {batch_size}"
    )
    return batches


def run_ade(pages: List[Dict]) -> List[Dict]:
    """
    Main ADE entry point.
    1. Build flat regions payload from all pages
    2. Batch if needed
    3. Call LLM for context threading
    4. Merge results back into pages
    5. Return enriched pages
    """
    logger.info(f"[ADEAgent] Starting ADE for {len(pages)} pages")

    regions_payload = _build_regions_payload(pages)

    if not regions_payload:
        logger.warning("[ADEAgent] No regions found -- skipping ADE")
        return pages

    # Batch processing
    batches = _batch_regions(regions_payload, batch_size=80)
    all_ade_results = []

    for batch_idx, batch in enumerate(batches):
        logger.info(
            f"[ADEAgent] Processing batch {batch_idx + 1}/{len(batches)} "
            f"({len(batch)} regions)"
        )
        result = _call_llm(batch)

        if result:
            all_ade_results.extend(result)
            logger.info(
                f"[ADEAgent] Batch {batch_idx + 1} -- "
                f"{len(result)} results received"
            )
        else:
            logger.error(
                f"[ADEAgent] Batch {batch_idx + 1} -- LLM returned no results, "
                f"skipping merge for this batch"
            )

    # Final safety filter -- only keep valid dict entries
    all_ade_results = [
        r for r in all_ade_results
        if isinstance(r, dict) and "region_id" in r
    ]

    if not all_ade_results:
        logger.error("[ADEAgent] No valid ADE results from any batch -- returning unthreaded pages")
        return pages

    enriched_pages = _merge_ade_results(pages, all_ade_results)

    logger.info(f"[ADEAgent] ADE complete -- {len(enriched_pages)} pages enriched")
    return enriched_pages