from pydantic import BaseModel
from typing import Optional, List, Literal


class BoundingBox(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float


class RegionBlock(BaseModel):
    region_id: str
    type: Literal["title", "heading", "body", "table", "image", "caption", "footer", "header", "sidebar", "callout", "unknown"]
    text: str
    bbox: BoundingBox
    sequence: int
    context_thread_id: Optional[str] = None
    context_role: Optional[Literal["heading", "body", "callout", "caption", "footnote", "continuation"]] = None
    continues_on_page: Optional[int] = None
    references_region: Optional[str] = None


class PageResult(BaseModel):
    page_number: int
    layout_type: Literal["single_col", "multi_col", "mixed", "table_heavy", "image_heavy", "unknown"]
    strategy: Literal["h_major", "v_major"]
    regions: List[RegionBlock]
    full_text: str


class ExtractResponse(BaseModel):
    file: str
    format: str
    total_pages: int
    pages: List[PageResult]


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None