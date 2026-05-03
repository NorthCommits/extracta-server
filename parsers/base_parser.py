import logging
from abc import ABC, abstractmethod
from typing import List, Dict

logger = logging.getLogger("extracta.parser.base")


class BaseParser(ABC):
    """
    Abstract base parser. All format-specific parsers inherit from this.

    Each parser is responsible for:
    - Opening the document
    - Extracting raw blocks per page/slide with bbox, text, and type hints
    - Returning a normalized list of pages, each with a list of raw blocks

    Raw block schema:
    {
        "text": str,
        "bbox": (x0, y0, x1, y1),   -- float tuple, top-left origin
        "block_type": str,            -- "text", "table", "image", "unknown"
        "font_size": float | None,
        "font_name": str | None,
        "is_bold": bool,
        "page_width": float,
        "page_height": float,
    }
    """

    def __init__(self, file_path: str):
        self.file_path = file_path
        logger.info(f"[{self.__class__.__name__}] Initialised for file: {file_path}")

    @abstractmethod
    def extract_pages(self) -> List[Dict]:
        """
        Extract all pages/slides from the document.

        Returns:
            List of page dicts:
            {
                "page_number": int,       -- 1-indexed
                "page_width": float,
                "page_height": float,
                "blocks": List[dict],     -- raw blocks as described above
            }
        """
        pass

    @abstractmethod
    def get_format(self) -> str:
        """Return the format string: 'pdf', 'pptx', 'docx', 'html'"""
        pass

    def normalize_bbox(self, x0: float, y0: float, x1: float, y1: float) -> tuple:
        """Ensure bbox is always (min_x, min_y, max_x, max_y)."""
        return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    def make_block(
        self,
        text: str,
        bbox: tuple,
        block_type: str = "text",
        font_size: float = None,
        font_name: str = None,
        is_bold: bool = False,
        page_width: float = 0.0,
        page_height: float = 0.0,
        extra: dict = None,
    ) -> dict:
        """Helper to build a normalized block dict."""
        block = {
            "text": text.strip(),
            "bbox": self.normalize_bbox(*bbox),
            "block_type": block_type,
            "font_size": font_size,
            "font_name": font_name,
            "is_bold": is_bold,
            "page_width": page_width,
            "page_height": page_height,
        }
        if extra:
            block.update(extra)
        return block

    def filter_empty_blocks(self, blocks: List[dict]) -> List[dict]:
        """Remove blocks with no meaningful text content."""
        filtered = [b for b in blocks if b.get("text", "").strip()]
        removed = len(blocks) - len(filtered)
        if removed > 0:
            logger.debug(f"[{self.__class__.__name__}] Removed {removed} empty blocks")
        return filtered

    def log_page_summary(self, page_number: int, blocks: List[dict]):
        logger.info(
            f"[{self.__class__.__name__}] Page {page_number} -- "
            f"{len(blocks)} blocks extracted"
        )