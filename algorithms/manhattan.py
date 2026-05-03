from typing import List, Dict, Tuple
import statistics


def compute_alignment_score(blocks: List[dict], axis: str = "x", tolerance: float = 5.0) -> float:
    """
    Measure how well blocks align on a given axis.
    High alignment = Manhattan (grid-like) layout.
    axis="x" checks left-edge alignment (column structure).
    axis="y" checks top-edge alignment (row structure).
    """
    if not blocks:
        return 0.0

    if axis == "x":
        edges = [b["bbox"][0] for b in blocks]  # x0 left edges
    else:
        edges = [b["bbox"][1] for b in blocks]  # y0 top edges

    if len(edges) < 2:
        return 1.0

    # Cluster edges within tolerance
    edges_sorted = sorted(edges)
    clusters = []
    current_cluster = [edges_sorted[0]]

    for edge in edges_sorted[1:]:
        if edge - current_cluster[-1] <= tolerance:
            current_cluster.append(edge)
        else:
            clusters.append(current_cluster)
            current_cluster = [edge]
    clusters.append(current_cluster)

    # Score = ratio of edges that fall into clusters of size >= 2
    aligned_count = sum(len(c) for c in clusters if len(c) >= 2)
    return aligned_count / len(edges)


def compute_spacing_uniformity(blocks: List[dict], axis: str = "x") -> float:
    """
    Measure how uniform the spacing between blocks is.
    Uniform spacing = Manhattan layout.
    axis="x" measures horizontal gaps between blocks.
    axis="y" measures vertical gaps between blocks.
    """
    if len(blocks) < 2:
        return 1.0

    if axis == "x":
        sorted_blocks = sorted(blocks, key=lambda b: b["bbox"][0])
        gaps = [
            sorted_blocks[i + 1]["bbox"][0] - sorted_blocks[i]["bbox"][2]
            for i in range(len(sorted_blocks) - 1)
        ]
    else:
        sorted_blocks = sorted(blocks, key=lambda b: b["bbox"][1])
        gaps = [
            sorted_blocks[i + 1]["bbox"][1] - sorted_blocks[i]["bbox"][3]
            for i in range(len(sorted_blocks) - 1)
        ]

    # Filter out negative gaps (overlapping blocks)
    gaps = [g for g in gaps if g > 0]

    if not gaps:
        return 1.0

    mean_gap = statistics.mean(gaps)
    if mean_gap == 0:
        return 1.0

    stdev_gap = statistics.stdev(gaps) if len(gaps) > 1 else 0.0
    cv = stdev_gap / mean_gap  # Coefficient of variation -- lower = more uniform

    # Normalize to 0-1 score where 1 = perfectly uniform
    uniformity = max(0.0, 1.0 - cv)
    return uniformity


def detect_overlapping_blocks(blocks: List[dict], tolerance: float = 2.0) -> int:
    """
    Count pairs of blocks with significant overlap.
    High overlap count = Non-Manhattan (complex/irregular) layout.
    """
    overlap_count = 0
    for i in range(len(blocks)):
        for j in range(i + 1, len(blocks)):
            a = blocks[i]["bbox"]
            b = blocks[j]["bbox"]
            x_overlap = min(a[2], b[2]) - max(a[0], b[0])
            y_overlap = min(a[3], b[3]) - max(a[1], b[1])
            if x_overlap > tolerance and y_overlap > tolerance:
                overlap_count += 1
    return overlap_count


def classify_layout(blocks: List[dict], page_width: float, page_height: float) -> Dict:
    """
    Classify page layout as Manhattan or Non-Manhattan.

    Manhattan: grid-like, aligned blocks, uniform spacing.
    Non-Manhattan: irregular, overlapping, or complex multi-region layouts.

    This classification drives downstream strategy selection and
    informs the XY-Cut segmentation aggressiveness.
    """
    if not blocks:
        return {
            "is_manhattan": True,
            "confidence": 1.0,
            "layout_type": "single_col",
            "x_alignment": 1.0,
            "y_alignment": 1.0,
            "x_uniformity": 1.0,
            "y_uniformity": 1.0,
            "overlap_count": 0,
        }

    x_alignment = compute_alignment_score(blocks, axis="x")
    y_alignment = compute_alignment_score(blocks, axis="y")
    x_uniformity = compute_spacing_uniformity(blocks, axis="x")
    y_uniformity = compute_spacing_uniformity(blocks, axis="y")
    overlap_count = detect_overlapping_blocks(blocks)

    # Manhattan score -- weighted combination
    manhattan_score = (
        x_alignment * 0.30 +
        y_alignment * 0.30 +
        x_uniformity * 0.20 +
        y_uniformity * 0.20
    )

    # Penalise for overlapping blocks
    overlap_penalty = min(0.4, overlap_count * 0.05)
    manhattan_score = max(0.0, manhattan_score - overlap_penalty)

    is_manhattan = manhattan_score >= 0.55

    # Estimate layout type from block distribution
    x_positions = [b["bbox"][0] for b in blocks]
    x_range = max(x_positions) - min(x_positions) if x_positions else 0
    col_spread_ratio = x_range / page_width if page_width > 0 else 0

    num_blocks = len(blocks)

    if not is_manhattan:
        layout_type = "mixed"
    elif overlap_count > 3:
        layout_type = "mixed"
    elif col_spread_ratio > 0.5 and num_blocks > 4:
        layout_type = "multi_col"
    elif num_blocks <= 3:
        layout_type = "single_col"
    else:
        # Check for table-heavy: many small blocks in a grid
        avg_block_height = statistics.mean([b["bbox"][3] - b["bbox"][1] for b in blocks])
        if avg_block_height < page_height * 0.05 and num_blocks > 10:
            layout_type = "table_heavy"
        else:
            layout_type = "single_col"

    return {
        "is_manhattan": is_manhattan,
        "confidence": manhattan_score,
        "layout_type": layout_type,
        "x_alignment": x_alignment,
        "y_alignment": y_alignment,
        "x_uniformity": x_uniformity,
        "y_uniformity": y_uniformity,
        "overlap_count": overlap_count,
    }