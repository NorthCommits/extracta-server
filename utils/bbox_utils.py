from typing import List, Tuple


def bbox_area(x0: float, y0: float, x1: float, y1: float) -> float:
    return max(0, x1 - x0) * max(0, y1 - y0)


def bbox_overlap_area(a: Tuple, b: Tuple) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0 = max(ax0, bx0)
    iy0 = max(ay0, by0)
    ix1 = min(ax1, bx1)
    iy1 = min(ay1, by1)
    return bbox_area(ix0, iy0, ix1, iy1)


def bbox_overlap_ratio(a: Tuple, b: Tuple) -> float:
    overlap = bbox_overlap_area(a, b)
    area_a = bbox_area(*a)
    area_b = bbox_area(*b)
    if area_a == 0 or area_b == 0:
        return 0.0
    return overlap / min(area_a, area_b)


def horizontal_gap(a: Tuple, b: Tuple) -> float:
    # Gap between a (left) and b (right) horizontally
    return b[0] - a[2]


def vertical_gap(a: Tuple, b: Tuple) -> float:
    # Gap between a (top) and b (bottom) vertically
    return b[1] - a[3]


def merge_bboxes(boxes: List[Tuple]) -> Tuple:
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes)
    y1 = max(b[3] for b in boxes)
    return (x0, y0, x1, y1)


def sort_blocks_top_left(blocks: List[dict]) -> List[dict]:
    # Sort by y0 first, then x0 -- top to bottom, left to right
    return sorted(blocks, key=lambda b: (b["bbox"][1], b["bbox"][0]))


def sort_blocks_left_top(blocks: List[dict]) -> List[dict]:
    # Sort by x0 first, then y0 -- left to right, top to bottom
    return sorted(blocks, key=lambda b: (b["bbox"][0], b["bbox"][1]))


def get_page_dimensions(blocks: List[dict]) -> Tuple[float, float]:
    if not blocks:
        return (0.0, 0.0)
    x1 = max(b["bbox"][2] for b in blocks)
    y1 = max(b["bbox"][3] for b in blocks)
    return (x1, y1)


def is_within_bounds(bbox: Tuple, page_w: float, page_h: float) -> bool:
    x0, y0, x1, y1 = bbox
    return x0 >= 0 and y0 >= 0 and x1 <= page_w and y1 <= page_h


def bbox_center(bbox: Tuple) -> Tuple[float, float]:
    x0, y0, x1, y1 = bbox
    return ((x0 + x1) / 2, (y0 + y1) / 2)


def blocks_in_column(blocks: List[dict], col_x0: float, col_x1: float, tolerance: float = 5.0) -> List[dict]:
    return [
        b for b in blocks
        if b["bbox"][0] >= col_x0 - tolerance and b["bbox"][2] <= col_x1 + tolerance
    ]