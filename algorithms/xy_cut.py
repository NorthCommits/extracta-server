import logging
from typing import List, Tuple, Optional, Dict

logger = logging.getLogger("extracta.xy_cut")


def _vertical_projection(blocks: List[dict], x_min: float, x_max: float, resolution: int = 500) -> List[float]:
    """Build vertical projection profile for a sub-region of the page."""
    width = x_max - x_min
    if width <= 0:
        return []
    profile = [0.0] * resolution
    bucket_width = width / resolution

    for block in blocks:
        bx0, _, bx1, _ = block["bbox"]
        # Clamp to region
        bx0 = max(bx0, x_min) - x_min
        bx1 = min(bx1, x_max) - x_min
        if bx1 <= bx0:
            continue
        text_len = len(block.get("text", "").strip())
        if text_len == 0:
            continue
        start = int(bx0 / bucket_width)
        end = int(bx1 / bucket_width)
        for i in range(max(0, start), min(resolution, end + 1)):
            profile[i] += text_len

    return profile


def _horizontal_projection(blocks: List[dict], y_min: float, y_max: float, resolution: int = 500) -> List[float]:
    """Build horizontal projection profile for a sub-region of the page."""
    height = y_max - y_min
    if height <= 0:
        return []
    profile = [0.0] * resolution
    bucket_height = height / resolution

    for block in blocks:
        _, by0, _, by1 = block["bbox"]
        by0 = max(by0, y_min) - y_min
        by1 = min(by1, y_max) - y_min
        if by1 <= by0:
            continue
        text_len = len(block.get("text", "").strip())
        if text_len == 0:
            continue
        start = int(by0 / bucket_height)
        end = int(by1 / bucket_height)
        for i in range(max(0, start), min(resolution, end + 1)):
            profile[i] += text_len

    return profile


def _find_cut_valleys(profile: List[float], threshold_ratio: float = 0.05, min_width: int = 3) -> List[Tuple[int, int]]:
    """
    Find valleys (whitespace gaps) in a projection profile suitable for cutting.
    min_width: minimum valley width in buckets to be considered a valid cut.
    """
    if not profile:
        return []

    max_val = max(profile) if max(profile) > 0 else 1
    threshold = max_val * threshold_ratio

    valleys = []
    in_valley = False
    valley_start = 0

    for i, val in enumerate(profile):
        if val <= threshold and not in_valley:
            in_valley = True
            valley_start = i
        elif val > threshold and in_valley:
            in_valley = False
            width = i - valley_start
            if width >= min_width:
                valleys.append((valley_start, i - 1))

    if in_valley:
        width = len(profile) - valley_start
        if width >= min_width:
            valleys.append((valley_start, len(profile) - 1))

    return valleys


def _best_cut(valleys: List[Tuple[int, int]]) -> Optional[int]:
    """Pick the widest valley as the best cut point. Returns midpoint index."""
    if not valleys:
        return None
    widest = max(valleys, key=lambda v: v[1] - v[0])
    return (widest[0] + widest[1]) // 2


def _blocks_in_region(blocks: List[dict], x0: float, y0: float, x1: float, y1: float, tolerance: float = 2.0) -> List[dict]:
    """Return blocks whose bbox center falls within the given region."""
    result = []
    for b in blocks:
        bx0, by0, bx1, by1 = b["bbox"]
        cx = (bx0 + bx1) / 2
        cy = (by0 + by1) / 2
        if x0 - tolerance <= cx <= x1 + tolerance and y0 - tolerance <= cy <= y1 + tolerance:
            result.append(b)
    return result


def xy_cut(
    blocks: List[dict],
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    strategy: str = "v_major",
    depth: int = 0,
    max_depth: int = 6,
    resolution: int = 500,
    threshold_ratio: float = 0.05,
    min_valley_width: int = 3,
) -> List[Dict]:
    """
    Recursive XY-Cut segmentation (Ha et al., 1995).

    strategy="v_major": try vertical cut first (finds columns), then horizontal.
    strategy="h_major": try horizontal cut first (finds rows), then vertical.

    Returns a flat ordered list of leaf regions, each containing the blocks
    that belong to that region in natural reading order.
    """
    region_blocks = _blocks_in_region(blocks, x0, y0, x1, y1)

    logger.debug(
        f"[XY-Cut] depth={depth} region=({x0:.1f},{y0:.1f},{x1:.1f},{y1:.1f}) "
        f"blocks={len(region_blocks)} strategy={strategy}"
    )

    if not region_blocks:
        logger.debug(f"[XY-Cut] depth={depth} -- no blocks in region, skipping")
        return []

    if depth >= max_depth or len(region_blocks) <= 1:
        logger.debug(f"[XY-Cut] depth={depth} -- leaf node with {len(region_blocks)} block(s)")
        return [{"blocks": region_blocks, "bbox": (x0, y0, x1, y1), "depth": depth}]

    width = x1 - x0
    height = y1 - y0

    # Attempt cuts in strategy-defined order
    primary_cut = "vertical" if strategy == "v_major" else "horizontal"
    secondary_cut = "horizontal" if primary_cut == "vertical" else "vertical"

    for cut_axis in [primary_cut, secondary_cut]:
        if cut_axis == "vertical":
            profile = _vertical_projection(region_blocks, x0, x1, resolution)
            valleys = _find_cut_valleys(profile, threshold_ratio, min_valley_width)
            cut_idx = _best_cut(valleys)

            if cut_idx is not None:
                cut_x = x0 + (cut_idx / resolution) * width
                logger.info(
                    f"[XY-Cut] depth={depth} -- vertical cut at x={cut_x:.1f} "
                    f"(valley idx={cut_idx}, valleys found={len(valleys)})"
                )
                left = xy_cut(
                    blocks, x0, y0, cut_x, y1,
                    strategy=strategy, depth=depth + 1, max_depth=max_depth,
                    resolution=resolution, threshold_ratio=threshold_ratio,
                    min_valley_width=min_valley_width
                )
                right = xy_cut(
                    blocks, cut_x, y0, x1, y1,
                    strategy=strategy, depth=depth + 1, max_depth=max_depth,
                    resolution=resolution, threshold_ratio=threshold_ratio,
                    min_valley_width=min_valley_width
                )
                # v_major: read left column fully before right column
                return left + right

        else:  # horizontal
            profile = _horizontal_projection(region_blocks, y0, y1, resolution)
            valleys = _find_cut_valleys(profile, threshold_ratio, min_valley_width)
            cut_idx = _best_cut(valleys)

            if cut_idx is not None:
                cut_y = y0 + (cut_idx / resolution) * height
                logger.info(
                    f"[XY-Cut] depth={depth} -- horizontal cut at y={cut_y:.1f} "
                    f"(valley idx={cut_idx}, valleys found={len(valleys)})"
                )
                top = xy_cut(
                    blocks, x0, y0, x1, cut_y,
                    strategy=strategy, depth=depth + 1, max_depth=max_depth,
                    resolution=resolution, threshold_ratio=threshold_ratio,
                    min_valley_width=min_valley_width
                )
                bottom = xy_cut(
                    blocks, x0, cut_y, x1, y1,
                    strategy=strategy, depth=depth + 1, max_depth=max_depth,
                    resolution=resolution, threshold_ratio=threshold_ratio,
                    min_valley_width=min_valley_width
                )
                # h_major: read top row fully before bottom row
                return top + bottom

    # No valid cut found -- this is a leaf node
    logger.debug(f"[XY-Cut] depth={depth} -- no cut found, treating as leaf with {len(region_blocks)} blocks")
    return [{"blocks": region_blocks, "bbox": (x0, y0, x1, y1), "depth": depth}]


def flatten_regions_to_blocks(regions: List[Dict], strategy: str = "v_major") -> List[dict]:
    """
    Flatten XY-Cut output regions into a single ordered list of blocks.
    Within each leaf region, sort blocks by natural reading order
    based on the dominant strategy.
    """
    ordered_blocks = []

    for region in regions:
        blocks = region["blocks"]
        if strategy == "v_major":
            # Within a column region: top to bottom
            sorted_blocks = sorted(blocks, key=lambda b: (b["bbox"][1], b["bbox"][0]))
        else:
            # Within a row region: left to right
            sorted_blocks = sorted(blocks, key=lambda b: (b["bbox"][0], b["bbox"][1]))
        ordered_blocks.extend(sorted_blocks)

    logger.info(f"[XY-Cut] Flattened {len(regions)} regions into {len(ordered_blocks)} ordered blocks")
    return ordered_blocks