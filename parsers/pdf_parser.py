import logging
from typing import List, Dict
import pymupdf
from parsers.base_parser import BaseParser

logger = logging.getLogger("extracta.parser.pdf")


class PDFParser(BaseParser):

    def get_format(self) -> str:
        return "pdf"

    def extract_pages(self) -> List[Dict]:
        logger.info(f"[PDFParser] Opening file: {self.file_path}")
        pages = []

        try:
            doc = pymupdf.open(self.file_path)
        except Exception as e:
            logger.error(f"[PDFParser] Failed to open file: {e}")
            raise

        logger.info(f"[PDFParser] Total pages: {doc.page_count}")

        for page_index in range(doc.page_count):
            page = doc[page_index]
            page_width = page.rect.width
            page_height = page.rect.height
            page_number = page_index + 1

            logger.debug(f"[PDFParser] Processing page {page_number} ({page_width:.1f}x{page_height:.1f})")

            blocks = []

            # Extract raw dict blocks -- includes lines, spans, font info
            raw = page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE)

            for block in raw.get("blocks", []):
                block_type = block.get("type", -1)

                # Type 0 = text block
                if block_type == 0:
                    text_block = self._process_text_block(
                        block, page_width, page_height
                    )
                    if text_block:
                        blocks.extend(text_block)

                # Type 1 = image block
                elif block_type == 1:
                    img_block = self._process_image_block(
                        block, page_width, page_height
                    )
                    if img_block:
                        blocks.append(img_block)

            # Extract tables separately using pymupdf table finder
            table_blocks = self._extract_tables(page, page_width, page_height)
            if table_blocks:
                logger.info(f"[PDFParser] Page {page_number} -- {len(table_blocks)} table(s) found")
                blocks.extend(table_blocks)

            blocks = self.filter_empty_blocks(blocks)
            self.log_page_summary(page_number, blocks)

            pages.append({
                "page_number": page_number,
                "page_width": page_width,
                "page_height": page_height,
                "blocks": blocks,
            })

        doc.close()
        logger.info(f"[PDFParser] Extraction complete -- {len(pages)} pages processed")
        return pages

    def _process_text_block(self, block: dict, page_width: float, page_height: float) -> List[dict]:
        """
        Process a pymupdf text block.
        Each block contains lines, each line contains spans.
        We group spans per line into a single block to preserve
        natural sentence/paragraph boundaries.
        """
        results = []
        bbox = block.get("bbox", (0, 0, 0, 0))
        lines = block.get("lines", [])

        # Collect all spans to detect dominant font
        all_spans = [span for line in lines for span in line.get("spans", [])]

        if not all_spans:
            return results

        # Dominant font size and boldness across spans
        font_sizes = [s.get("size", 0) for s in all_spans if s.get("size", 0) > 0]
        font_size = max(font_sizes) if font_sizes else None
        font_name = all_spans[0].get("font", None) if all_spans else None
        is_bold = self._detect_bold(all_spans)

        # Full block text -- preserve paragraph structure
        full_text = "\n".join(
            " ".join(span.get("text", "") for span in line.get("spans", []))
            for line in lines
        ).strip()

        if not full_text:
            return results

        block_type = self._classify_text_block(font_size, is_bold, full_text, page_height)

        results.append(self.make_block(
            text=full_text,
            bbox=bbox,
            block_type=block_type,
            font_size=font_size,
            font_name=font_name,
            is_bold=is_bold,
            page_width=page_width,
            page_height=page_height,
        ))

        return results

    def _process_image_block(self, block: dict, page_width: float, page_height: float) -> dict:
        """Process an image block -- capture position, mark as image type."""
        bbox = block.get("bbox", (0, 0, 0, 0))
        logger.debug(f"[PDFParser] Image block at bbox={bbox}")
        return self.make_block(
            text="[IMAGE]",
            bbox=bbox,
            block_type="image",
            page_width=page_width,
            page_height=page_height,
        )

    def _extract_tables(self, page, page_width: float, page_height: float) -> List[dict]:
        """
        Use pymupdf's built-in table finder to detect and extract tables.
        Converts each table to markdown for structured representation.
        """
        results = []
        try:
            tables = page.find_tables()
            for table in tables:
                bbox = table.bbox
                try:
                    markdown = table.to_markdown()
                except Exception:
                    markdown = "[TABLE -- could not render]"

                results.append(self.make_block(
                    text=markdown,
                    bbox=bbox,
                    block_type="table",
                    page_width=page_width,
                    page_height=page_height,
                ))
        except Exception as e:
            logger.warning(f"[PDFParser] Table extraction failed: {e}")

        return results

    def _detect_bold(self, spans: List[dict]) -> bool:
        """Detect if majority of spans in a block are bold."""
        if not spans:
            return False
        bold_count = sum(
            1 for s in spans
            if "bold" in s.get("font", "").lower() or (s.get("flags", 0) & 2**4)
        )
        return bold_count > len(spans) / 2

    def _classify_text_block(
        self,
        font_size: float,
        is_bold: bool,
        text: str,
        page_height: float
    ) -> str:
        """
        Heuristic classification of text blocks into semantic types.
        Uses font size relative to page height and boldness.
        """
        if font_size is None:
            return "text"

        # Relative font size to page -- titles tend to be large
        relative_size = font_size / page_height if page_height > 0 else 0

        if relative_size > 0.04 and is_bold:
            return "title"
        elif relative_size > 0.025 and is_bold:
            return "heading"
        elif relative_size < 0.012:
            return "footer" if len(text) < 100 else "text"
        else:
            return "text"