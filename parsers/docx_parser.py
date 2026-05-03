import logging
from typing import List, Dict, Optional
from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt
from parsers.base_parser import BaseParser

logger = logging.getLogger("extracta.parser.docx")

# Standard A4 page dimensions in points (fallback)
DEFAULT_PAGE_WIDTH_PT = 595.0
DEFAULT_PAGE_HEIGHT_PT = 842.0

# Approximate line height multiplier for bbox simulation
LINE_HEIGHT_MULTIPLIER = 1.4


class DOCXParser(BaseParser):

    def get_format(self) -> str:
        return "docx"

    def extract_pages(self) -> List[Dict]:
        logger.info(f"[DOCXParser] Opening file: {self.file_path}")

        try:
            doc = Document(self.file_path)
        except Exception as e:
            logger.error(f"[DOCXParser] Failed to open file: {e}")
            raise

        page_width, page_height = self._get_page_dimensions(doc)
        logger.info(
            f"[DOCXParser] Page dimensions: {page_width:.1f}x{page_height:.1f} pt"
        )

        # DOCX has no native page boundaries -- we simulate pages
        # by tracking cumulative y position and breaking at page_height
        all_blocks = self._extract_all_blocks(doc, page_width, page_height)
        pages = self._paginate_blocks(all_blocks, page_width, page_height)

        logger.info(f"[DOCXParser] Extraction complete -- {len(pages)} simulated pages")
        return pages

    def _get_page_dimensions(self, doc: Document):
        """Extract page dimensions from document section settings."""
        try:
            section = doc.sections[0]
            width = section.page_width.pt if section.page_width else DEFAULT_PAGE_WIDTH_PT
            height = section.page_height.pt if section.page_height else DEFAULT_PAGE_HEIGHT_PT

            # Account for margins
            left_margin = section.left_margin.pt if section.left_margin else 72.0
            right_margin = section.right_margin.pt if section.right_margin else 72.0
            top_margin = section.top_margin.pt if section.top_margin else 72.0

            usable_width = width - left_margin - right_margin
            logger.debug(
                f"[DOCXParser] Page: {width:.1f}x{height:.1f} pt | "
                f"Margins: L={left_margin:.1f} R={right_margin:.1f} T={top_margin:.1f}"
            )
            return usable_width, height

        except Exception as e:
            logger.warning(f"[DOCXParser] Could not read page dimensions: {e} -- using defaults")
            return DEFAULT_PAGE_WIDTH_PT, DEFAULT_PAGE_HEIGHT_PT

    def _extract_all_blocks(
        self, doc: Document, page_width: float, page_height: float
    ) -> List[dict]:
        """
        Extract all content blocks from the DOCX document.
        DOCX is linear -- paragraphs and tables in document order.
        We simulate bounding boxes using cumulative y-position tracking.
        """
        blocks = []
        cursor_y = 72.0  # Start after top margin

        # Walk document body children to preserve paragraph + table order
        body = doc.element.body

        for child in body.iterchildren():
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag

            if tag == "p":
                # Paragraph
                para_block, cursor_y = self._process_paragraph_element(
                    child, doc, cursor_y, page_width, page_height
                )
                if para_block:
                    blocks.append(para_block)

            elif tag == "tbl":
                # Table
                table_block, cursor_y = self._process_table_element(
                    child, doc, cursor_y, page_width, page_height
                )
                if table_block:
                    blocks.append(table_block)

            elif tag == "sectPr":
                # Section properties -- page break signal
                logger.debug("[DOCXParser] Section break encountered")
                continue

        logger.info(f"[DOCXParser] Extracted {len(blocks)} raw blocks from document body")
        return blocks

    def _process_paragraph_element(
        self, elem, doc: Document, cursor_y: float, page_width: float, page_height: float
    ):
        """Process a paragraph XML element into a block."""
        from docx.text.paragraph import Paragraph
        try:
            para = Paragraph(elem, doc.element.body)
            text = para.text.strip()
            if not text:
                cursor_y += 5.0  # Small spacing for empty paragraphs
                return None, cursor_y

            font_size, is_bold, font_name = self._get_paragraph_font(para)
            block_type = self._classify_paragraph(para, font_size, is_bold, page_height)

            block_height = (font_size or 11.0) * LINE_HEIGHT_MULTIPLIER
            bbox = (0.0, cursor_y, page_width, cursor_y + block_height)

            block = self.make_block(
                text=text,
                bbox=bbox,
                block_type=block_type,
                font_size=font_size,
                font_name=font_name,
                is_bold=is_bold,
                page_width=page_width,
                page_height=page_height,
            )

            cursor_y += block_height + 4.0  # spacing between paragraphs
            return block, cursor_y

        except Exception as e:
            logger.warning(f"[DOCXParser] Paragraph processing error: {e}")
            return None, cursor_y

    def _process_table_element(
        self, elem, doc: Document, cursor_y: float, page_width: float, page_height: float
    ):
        """Process a table XML element into a single markdown block."""
        from docx.table import Table
        try:
            table = Table(elem, doc.element.body)
            rows = []

            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                rows.append(" | ".join(cells))

            if not rows:
                return None, cursor_y

            header = rows[0]
            separator = " | ".join(["---"] * len(rows[0].split(" | ")))
            table_text = "\n".join([header, separator] + rows[1:])

            num_rows = len(rows)
            table_height = num_rows * 16.0  # approximate row height in points
            bbox = (0.0, cursor_y, page_width, cursor_y + table_height)

            block = self.make_block(
                text=table_text,
                bbox=bbox,
                block_type="table",
                page_width=page_width,
                page_height=page_height,
            )

            logger.debug(f"[DOCXParser] Table extracted: {num_rows} rows")
            cursor_y += table_height + 8.0
            return block, cursor_y

        except Exception as e:
            logger.warning(f"[DOCXParser] Table processing error: {e}")
            return None, cursor_y

    def _get_paragraph_font(self, para) -> tuple:
        """
        Extract dominant font size, boldness, and font name from paragraph runs.
        Falls back to paragraph style if runs have no explicit font info.
        """
        font_sizes = []
        bold_flags = []
        font_names = []

        for run in para.runs:
            if run.font.size:
                font_sizes.append(run.font.size.pt)
            if run.font.bold is not None:
                bold_flags.append(run.font.bold)
            if run.font.name:
                font_names.append(run.font.name)

        # Fallback to style font size
        if not font_sizes:
            try:
                style_font = para.style.font
                if style_font.size:
                    font_sizes.append(style_font.size.pt)
            except Exception:
                pass

        font_size = max(font_sizes) if font_sizes else 11.0
        is_bold = any(bold_flags) if bold_flags else False
        font_name = font_names[0] if font_names else None

        return font_size, is_bold, font_name

    def _classify_paragraph(self, para, font_size: float, is_bold: bool, page_height: float) -> str:
        """
        Classify paragraph block type using style name and font heuristics.
        Word heading styles: Heading 1-9, Title, Subtitle.
        """
        style_name = ""
        try:
            style_name = para.style.name.lower() if para.style and para.style.name else ""
        except Exception:
            pass

        if "title" in style_name:
            return "title"
        elif "heading 1" in style_name:
            return "title"
        elif "heading" in style_name:
            return "heading"
        elif "subtitle" in style_name:
            return "heading"
        elif "caption" in style_name:
            return "caption"
        elif "footer" in style_name:
            return "footer"
        elif "header" in style_name:
            return "header"

        # Font size heuristics as fallback
        if font_size is None:
            return "text"

        relative_size = font_size / page_height if page_height > 0 else 0

        if relative_size > 0.03 and is_bold:
            return "title"
        elif relative_size > 0.02 and is_bold:
            return "heading"
        elif font_size < 9.0:
            return "footer"
        else:
            return "text"

    def _paginate_blocks(
        self, blocks: List[dict], page_width: float, page_height: float
    ) -> List[Dict]:
        """
        Split flat block list into simulated pages based on y-position overflow.
        DOCX has no real page boundaries -- we simulate by page_height.
        """
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

            # If block overflows current page, start new page
            if by1 - page_y_offset > page_height and current_page_blocks:
                pages.append({
                    "page_number": current_page,
                    "page_width": page_width,
                    "page_height": page_height,
                    "blocks": self.filter_empty_blocks(current_page_blocks),
                })
                self.log_page_summary(current_page, current_page_blocks)
                current_page += 1
                page_y_offset = by0
                current_page_blocks = []

            # Adjust bbox to be relative to current page
            adjusted_block = dict(block)
            adjusted_block["bbox"] = (
                bx0,
                by0 - page_y_offset,
                bx1,
                by1 - page_y_offset,
            )
            current_page_blocks.append(adjusted_block)

        # Last page
        if current_page_blocks:
            pages.append({
                "page_number": current_page,
                "page_width": page_width,
                "page_height": page_height,
                "blocks": self.filter_empty_blocks(current_page_blocks),
            })
            self.log_page_summary(current_page, current_page_blocks)

        return pages