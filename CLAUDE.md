# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies (Python 3.10+)
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Run the dev server
uvicorn main:app --reload --port 8000

# Hit the API manually
curl -X POST http://localhost:8000/api/v1/extract \
  -F "file=@/path/to/document.pdf"
```

No test suite exists yet. Validate changes by running the server and posting documents against `POST /api/v1/extract`.

## Environment

Copy `.env` and set:
```
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4o        # default
MAX_FILE_SIZE_MB=50        # default
TEMP_DIR=/tmp/extracta     # default
```

## Architecture

The server has a single endpoint (`POST /api/v1/extract`) that runs a 5-stage pipeline on uploaded documents (PDF, PPTX, DOCX, HTML) and returns structured JSON with layout-aware, context-threaded blocks.

### Pipeline stages (`pipeline/`)

1. **Parse** (`orchestrator.py` → `parsers/`) — Format-specific parser converts the document into a normalized list of page dicts. Each page has `page_number`, `page_width`, `page_height`, and `blocks` (list of raw block dicts with `text`, `bbox`, `block_type`, `font_size`, etc.).

2. **Detect** (`detector.py`) — Per page, runs two algorithms:
   - **Manhattan classification** (`algorithms/manhattan.py`) — checks whether blocks align on a grid (Manhattan layout) or overlap/scatter (non-Manhattan/mixed).
   - **Projection profiles** (`algorithms/projection.py`) — builds vertical and horizontal text-density profiles to find whitespace gaps. `dominant_axis()` returns `v_major` (multi-column, read top-down per column) or `h_major` (stacked sections, read left-right per row).
   - Outputs `layout_type` (single_col, multi_col, mixed, table_heavy, image_heavy) and `strategy` (h_major / v_major) per page.

3. **Extract** (`extractor.py`) — Applies **XY-Cut** (`algorithms/xy_cut.py`) to recursively bisect the page along whitespace valleys, producing an ordered list of blocks in natural human reading order. `single_col` and `table_heavy` pages skip XY-Cut and use a direct top-to-bottom sort.

4. **ADE** (`ade_agent.py`) — All ordered blocks across all pages are flattened into a payload (text capped at 500 chars/block) and sent to OpenAI in batches of 80 regions. The LLM assigns `context_thread_id`, `context_role`, `continues_on_page`, and `references_region` to each block. Results are merged back by `region_id` (`p{page}_r{sequence}`).

5. **Build response** (`orchestrator.py:_build_response`) — Converts the enriched pipeline dicts into the Pydantic `ExtractResponse` schema.

### Parsers (`parsers/`)

All parsers extend `BaseParser` (ABC). Key contract: `extract_pages()` returns `List[Dict]` with the normalized page/block schema described above. Use `self.make_block()` and `self.filter_empty_blocks()` from the base class when building blocks.

- `PDFParser` — PyMuPDF (`fitz`)
- `PPTXParser` — python-pptx
- `DOCXParser` — python-docx
- `HTMLParser` — BeautifulSoup4 + lxml

### Data flow types

Internal pipeline passes plain `dict` objects between stages. Only at the final step do they get validated into Pydantic models (`models/schemas.py`). The `region_id` key (`p{page_number}_r{sequence}`) is the join key between the extractor and the ADE merge step — keep this format consistent if touching either.

### Key schema types (`models/schemas.py`)

- `RegionBlock` — one extracted text/table/image region with bbox, type, sequence, and all ADE threading fields
- `PageResult` — one page: `layout_type`, `strategy`, list of `RegionBlock`, `full_text`
- `ExtractResponse` — top-level response: file name, format, page count, list of `PageResult`
