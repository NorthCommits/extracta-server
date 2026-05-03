from typing import List, Tuple, Dict
import statistics


def build_vertical_projection(blocks: List[dict], page_width: float, resolution: int = 100) -> List[float]:
    """
    Vertical projection profile -- scans left to right.
    Each bucket represents a vertical slice of the page.
    Value = total text density in that slice.
    High values = text present. Low/zero values = whitespace gaps (column separators).
    """
    profile = [0.0] * resolution
    bucket_width = page_width / resolution

    for block in blocks:
        x0, _, x1, _ = block["bbox"]
        text_len = len(block.get("text", "").strip())
        if text_len == 0:
            continue
        start_bucket = int(x0 / bucket_width)
        end_bucket = int(x1 / bucket_width)
        for i in range(max(0, start_bucket), min(resolution, end_bucket + 1)):
            profile[i] += text_len

    return profile


def build_horizontal_projection(blocks: List[dict], page_height: float, resolution: int = 100) -> List[float]:
    """
    Horizontal projection profile -- scans top to bottom.
    Each bucket represents a horizontal slice of the page.
    Value = total text density in that slice.
    High values = text present. Low/zero values = whitespace gaps (row separators).
    """
    profile = [0.0] * resolution
    bucket_height = page_height / resolution

    for block in blocks:
        _, y0, _, y1 = block["bbox"]
        text_len = len(block.get("text", "").strip())
        if text_len == 0:
            continue
        start_bucket = int(y0 / bucket_height)
        end_bucket = int(y1 / bucket_height)
        for i in range(max(0, start_bucket), min(resolution, end_bucket + 1)):
            profile[i] += text_len

    return profile


def find_valleys(profile: List[float], threshold_ratio: float = 0.1) -> List[Tuple[int, int]]:
    """
    Find whitespace valleys in a projection profile.
    A valley is a contiguous run of buckets below the threshold.
    Returns list of (start, end) index pairs for each valley.
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
            valleys.append((valley_start, i - 1))

    if in_valley:
        valleys.append((valley_start, len(profile) - 1))

    return valleys


def valley_widths(valleys: List[Tuple[int, int]]) -> List[int]:
    return [end - start + 1 for start, end in valleys]


def dominant_axis(
    v_profile: List[float],
    h_profile: List[float],
    page_width: float,
    page_height: float
) -> Dict:
    """
    Compare vertical vs horizontal projection valleys to determine
    which axis has stronger whitespace separation.

    V-major: strong vertical whitespace gaps = multiple columns
             reading order should be top-to-bottom within each column

    H-major: strong horizontal whitespace gaps = stacked sections
             reading order should be left-to-right across each row

    Returns dict with strategy and supporting metrics.
    """
    v_valleys = find_valleys(v_profile)
    h_valleys = find_valleys(h_profile)

    v_widths = valley_widths(v_valleys)
    h_widths = valley_widths(h_valleys)

    avg_v_gap = statistics.mean(v_widths) if v_widths else 0
    avg_h_gap = statistics.mean(h_widths) if h_widths else 0

    num_v_valleys = len(v_valleys)
    num_h_valleys = len(h_valleys)

    # Score: combination of gap width and count
    v_score = avg_v_gap * num_v_valleys
    h_score = avg_h_gap * num_h_valleys

    if v_score >= h_score:
        strategy = "v_major"
    else:
        strategy = "h_major"

    return {
        "strategy": strategy,
        "v_valleys": v_valleys,
        "h_valleys": h_valleys,
        "avg_v_gap": avg_v_gap,
        "avg_h_gap": avg_h_gap,
        "v_score": v_score,
        "h_score": h_score,
        "num_columns_estimate": num_v_valleys + 1 if num_v_valleys > 0 else 1,
        "num_rows_estimate": num_h_valleys + 1 if num_h_valleys > 0 else 1,
    }