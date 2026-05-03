# Extracta Server

Layout-aware, context-threaded document extraction API. Upload a PDF, PPTX, DOCX, or HTML file and get back structured JSON — every block in correct reading order, grouped into logical narrative threads by an LLM.

---

## How it works

Most document parsers return a flat dump of text. Extracta does three things they don't:

1. **Layout detection** — classifies each page as single-column, multi-column, mixed, table-heavy, or image-heavy using projection profiles and Manhattan grid analysis.
2. **Reading order** — applies recursive XY-Cut segmentation (Ha et al., 1995) to find column/row boundaries and sort blocks the way a human would read them.
3. **Context threading** — sends the ordered blocks to an LLM, which groups them into logical threads (heading → body → callout), flags cross-page continuations, and links captions to their figures.

```
Upload file
    │
    ▼
┌─────────────┐     ┌──────────────────┐     ┌──────────────────┐
│   Parser    │────▶│ Layout Detector  │────▶│    Extractor     │
│ PDF/PPTX/   │     │ Manhattan + XY   │     │  XY-Cut reading  │
│ DOCX / HTML │     │ projection algo  │     │  order sort      │
└─────────────┘     └──────────────────┘     └────────┬─────────┘
                                                       │
                                                       ▼
                                             ┌──────────────────┐
                                             │   ADE Agent      │
                                             │  LLM context     │
                                             │  threading       │
                                             └────────┬─────────┘
                                                       │
                                                       ▼
                                             Structured JSON response
```

---

## Quickstart

**Prerequisites:** Python 3.10+, an OpenAI API key.

```bash
git clone <repo>
cd extracta-server

python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env and set OPENAI_API_KEY

python -m uvicorn main:app --port 8000 --reload
```

> **Note:** Always use `python -m uvicorn` (not bare `uvicorn`) to ensure the venv's interpreter is used.

---

## Configuration

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | required | OpenAI API key |
| `OPENAI_MODEL` | `gpt-4o` | Model used for ADE context threading |
| `MAX_FILE_SIZE_MB` | `50` | Upload size limit |
| `TEMP_DIR` | `/tmp/extracta` | Temporary file storage during processing |

---

## API

### `POST /api/v1/extract`

Upload a document and receive structured extraction results.

**Request:** `multipart/form-data` with a single `file` field.

```bash
curl -X POST http://localhost:8000/api/v1/extract \
  -F "file=@report.pdf"
```

**Response:**

```jsonc
{
  "file": "report.pdf",
  "format": "pdf",
  "total_pages": 3,
  "pages": [
    {
      "page_number": 1,
      "layout_type": "multi_col",   // single_col | multi_col | mixed | table_heavy | image_heavy
      "strategy": "v_major",        // v_major (columns) | h_major (rows)
      "full_text": "Introduction\n\nThis paper presents...",
      "regions": [
        {
          "region_id": "p1_r1",
          "type": "heading",         // title | heading | body | table | image | caption | footer | header | sidebar | callout
          "text": "Introduction",
          "bbox": { "x0": 72, "y0": 68, "x1": 290, "y1": 84 },
          "sequence": 1,
          "context_thread_id": "thread_001",
          "context_role": "heading", // heading | body | callout | caption | footnote | continuation
          "continues_on_page": null,
          "references_region": null
        }
      ]
    }
  ]
}
```

**Errors:**

| Status | Cause |
|---|---|
| `400` | Unsupported format or file exceeds size limit |
| `422` | Validation error (e.g. missing filename) |
| `500` | Internal pipeline error |

Interactive docs are available at `http://localhost:8000/docs`.

---

## Supported formats

| Format | Parser | Notes |
|---|---|---|
| PDF | PyMuPDF | Text + image blocks with precise bounding boxes |
| PPTX | python-pptx | Each slide = one page |
| DOCX | python-docx | Synthesises page boundaries from content flow |
| HTML | BeautifulSoup4 + lxml | Semantic tags inform block type hints |

---

## Project structure

```
extracta-server/
├── main.py                  # FastAPI app + CORS
├── config.py                # Env vars
├── routers/
│   └── extract.py           # POST /api/v1/extract — upload, validate, cleanup
├── pipeline/
│   ├── orchestrator.py      # Wires all 5 stages together
│   ├── detector.py          # Layout detection (Manhattan + projection)
│   ├── extractor.py         # Reading order via XY-Cut
│   └── ade_agent.py         # LLM context threading (batched, 80 regions/call)
├── parsers/
│   ├── base_parser.py       # ABC with shared helpers
│   ├── pdf_parser.py
│   ├── pptx_parser.py
│   ├── docx_parser.py
│   └── html_parser.py
├── algorithms/
│   ├── xy_cut.py            # Recursive XY-Cut segmentation
│   ├── projection.py        # Vertical/horizontal projection profiles
│   └── manhattan.py         # Grid alignment / overlap detection
├── models/
│   └── schemas.py           # Pydantic response models
└── utils/
    └── bbox_utils.py        # Bounding box helpers
```

---

## License

MIT
