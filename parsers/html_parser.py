import logging
from typing import List, Dict, Optional, Tuple
from bs4 import BeautifulSoup, Tag, NavigableString
from parsers.base_parser import BaseParser

logger = logging.getLogger("extracta.parser.html")

# Simulated page dimensions for HTML (no native concept of pages)
DEFAULT_PAGE_WIDTH_PT = 595.0
DEFAULT_PAGE_HEIGHT_PT = 842.0

# Font size mappings for HTML heading tags
HEADING_FONT_SIZES = {
    "h1": 28.0,
    "h2": 24.0,
    "h3": 20.0,
    "h4": 16.0,
    "h5": 14.0,
    "h6": 12.0,
}

# Tags we skip entirely
SKIP_TAGS = {
    "script", "style", "meta", "link", "head",
    "noscript", "iframe", "svg", "canvas", "nav",
    "footer", "aside"
}

# Block-level tags that form natural content boundaries
BLOCK_TAGS = {
    "p", "div", "section", "article", "main", "header",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "blockquote", "pre", "code",
    "table", "figure", "figcaption", "caption"
}


class HTMLParser(BaseParser):

    def get_format(self) -> str:
        return "html"

    def extract_pages(self) -> List[Dict]:
        logger.info(f"[HTMLParser] Opening file: {self.file_path}")

        try:
            with open(self.file_path, "r", encoding="utf-8", errors="replace") as f:
                raw_html = f.read()
        except Exception as e:
            logger.error(f"[HTMLParser] Failed to read file: {e}")
            raise

        soup = BeautifulSoup(raw_html, "html.parser")
        logger.info(f"[HTMLParser] HTML parsed successfully")

        # Remove unwanted tags
        for tag in soup(list(SKIP_TAGS)):
            tag.decompose()

        page_width = DEFAULT_PAGE_WIDTH_PT
        page_height = DEFAULT_PAGE_HEIGHT_PT

        blocks = self._extract_blocks(soup, page_width, page_height)
        blocks = self.filter_empty_blocks(blocks)

        logger.info(f"[HTMLParser] Total blocks extracted: {len(blocks)}")

        pages = self._paginate_blocks(blocks, page_width, page_height)
        logger.info(f"[HTMLParser] Extraction complete -- {len(pages)} simulated pages")
        return pages

    def _extract_blocks(
        self, soup: BeautifulSoup, page_width: float, page_height: float
    ) -> List[dict]:
        """
        Walk the DOM tree and extract content blocks.
        We use a cursor-based y-position simulation to assign bboxes.
        Block-level elements get their own block.
        Inline content is grouped under their nearest block parent.
        """
        blocks = []
        cursor_y = 0.0

        # Find the main content root
        root = (
            soup.find("main") or
            soup.find("article") or
            soup.find("body") or
            soup
        )

        logger.debug(f"[HTMLParser] Content root tag: {root.name if hasattr(root, 'name') else 'document'}")

        for element in root.children:
            if isinstance(element, NavigableString):
                text = element.strip()
                if text:
                    block, cursor_y = self._make_text_block(
                        text=text,
                        tag_name="p",
                        cursor_y=cursor_y,
                        page_width=page_width,
                        page_height=page_height,
                    )
                    if block:
                        blocks.append(block)
                continue

            if not isinstance(element, Tag):
                continue

            extracted, cursor_y = self._process_element(
                element, cursor_y, page_width, page_height, depth=0
            )
            blocks.extend(extracted)

        return blocks

    def _process_element(
        self,
        element: Tag,
        cursor_y: float,
        page_width: float,
        page_height: float,
        depth: int = 0,
    ) -> Tuple[List[dict], float]:
        """
        Recursively process a DOM element.
        Block-level elements are extracted as individual blocks.
        Nested blocks are recursed into.
        """
        blocks = []

        tag_name = element.name if element.name else ""

        if tag_name in SKIP_TAGS:
            return blocks, cursor_y

        # Table gets special handling
        if tag_name == "table":
            block, cursor_y = self._process_table(element, cursor_y, page_width, page_height)
            if block:
                blocks.append(block)
            return blocks, cursor_y

        # Heading tags
        if tag_name in HEADING_FONT_SIZES:
            text = element.get_text(separator=" ", strip=True)
            if text:
                block, cursor_y = self._make_text_block(
                    text=text,
                    tag_name=tag_name,
                    cursor_y=cursor_y,
                    page_width=page_width,
                    page_height=page_height,
                )
                if block:
                    blocks.append(block)
            return blocks, cursor_y

        # List items
        if tag_name == "li":
            text = element.get_text(separator=" ", strip=True)
            if text:
                text = f"• {text}"
                block, cursor_y = self._make_text_block(
                    text=text,
                    tag_name="li",
                    cursor_y=cursor_y,
                    page_width=page_width,
                    page_height=page_height,
                )
                if block:
                    blocks.append(block)
            return blocks, cursor_y

        # Block-level tags with direct text content
        if tag_name in BLOCK_TAGS:
            # Check if this element has nested block children
            has_block_children = any(
                isinstance(c, Tag) and c.name in BLOCK_TAGS
                for c in element.children
            )

            if has_block_children:
                # Recurse into children
                for child in element.children:
                    if isinstance(child, NavigableString):
                        text = child.strip()
                        if text:
                            block, cursor_y = self._make_text_block(
                                text=text,
                                tag_name="p",
                                cursor_y=cursor_y,
                                page_width=page_width,
                                page_height=page_height,
                            )
                            if block:
                                blocks.append(block)
                    elif isinstance(child, Tag):
                        child_blocks, cursor_y = self._process_element(
                            child, cursor_y, page_width, page_height, depth + 1
                        )
                        blocks.extend(child_blocks)
            else:
                # Leaf block -- extract text directly
                text = element.get_text(separator=" ", strip=True)
                if text:
                    block, cursor_y = self._make_text_block(
                        text=text,
                        tag_name=tag_name,
                        cursor_y=cursor_y,
                        page_width=page_width,
                        page_height=page_height,
                    )
                    if block:
                        blocks.append(block)

        return blocks, cursor_y

    def _process_table(
        self,
        element: Tag,
        cursor_y: float,
        page_width: float,
        page_height: float,
    ) -> Tuple[Optional[dict], float]:
        """Extract HTML table as markdown-style text block."""
        rows = []

        for tr in element.find_all("tr"):
            cells = []
            for cell in tr.find_all(["td", "th"]):
                cells.append(cell.get_text(separator=" ", strip=True))
            if cells:
                rows.append(" | ".join(cells))

        if not rows:
            return None, cursor_y

        header = rows[0]
        separator = " | ".join(["---"] * len(rows[0].split(" | ")))
        table_text = "\n".join([header, separator] + rows[1:])

        num_rows = len(rows)
        table_height = num_rows * 16.0
        bbox = (0.0, cursor_y, page_width, cursor_y + table_height)

        block = self.make_block(
            text=table_text,
            bbox=bbox,
            block_type="table",
            page_width=page_width,
            page_height=page_height,
        )

        logger.debug(f"[HTMLParser] Table extracted: {num_rows} rows")
        cursor_y += table_height + 8.0
        return block, cursor_y

    def _make_text_block(
        self,
        text: str,
        tag_name: str,
        cursor_y: float,
        page_width: float,
        page_height: float,
    ) -> Tuple[Optional[dict], float]:
        """
        Create a text block with simulated bbox from tag type and cursor position.
        """
        text = text.strip()
        if not text:
            return None, cursor_y

        font_size = HEADING_FONT_SIZES.get(tag_name, 11.0)
        is_bold = tag_name in ("h1", "h2", "h3", "h4", "h5", "h6")
        block_type = self._classify_tag(tag_name)

        line_height = font_size * 1.4
        # Estimate block height by text length and line width
        chars_per_line = max(1, int(page_width / (font_size * 0.55)))
        num_lines = max(1, len(text) // chars_per_line + 1)
        block_height = line_height * num_lines

        bbox = (0.0, cursor_y, page_width, cursor_y + block_height)

        block = self.make_block(
            text=text,
            bbox=bbox,
            block_type=block_type,
            font_size=font_size,
            is_bold=is_bold,
            page_width=page_width,
            page_height=page_height,
        )

        cursor_y += block_height + 6.0
        return block, cursor_y

    def _classify_tag(self, tag_name: str) -> str:
        """Map HTML tag name to extracta block type."""
        mapping = {
            "h1": "title",
            "h2": "title",
            "h3": "heading",
            "h4": "heading",
            "h5": "heading",
            "h6": "heading",
            "p": "text",
            "li": "text",
            "blockquote": "text",
            "pre": "text",
            "code": "text",
            "figcaption": "caption",
            "caption": "caption",
            "table": "table",
        }
        return mapping.get(tag_name, "text")

    def _paginate_blocks(
        self, blocks: List[dict], page_width: float, page_height: float
    ) -> List[Dict]:
        """Split flat block list into simulated pages based on y overflow."""
        if not blocks:
            return [{
                "page_number": 1,
                "page_width": page_width,
                "page_height": page_height,
                "blocks": []
            }]

        pages = []
        current_page_blocks = []
        current_page = 1
        page_y_offset = 0.0

        for block in blocks:
            bx0, by0, bx1, by1 = block["bbox"]

            if by1 - page_y_offset > page_height and current_page_blocks:
                pages.append({
                    "page_number": current_page,
                    "page_width": page_width,
                    "page_height": page_height,
                    "blocks": current_page_blocks,
                })
                self.log_page_summary(current_page, current_page_blocks)
                current_page += 1
                page_y_offset = by0
                current_page_blocks = []

            adjusted_block = dict(block)
            adjusted_block["bbox"] = (
                bx0,
                by0 - page_y_offset,
                bx1,
                by1 - page_y_offset,
            )
            current_page_blocks.append(adjusted_block)

        if current_page_blocks:
            pages.append({
                "page_number": current_page,
                "page_width": page_width,
                "page_height": page_height,
                "blocks": current_page_blocks,
            })
            self.log_page_summary(current_page, current_page_blocks)

        return pages