from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import extract

app = FastAPI(
    title="Extracta Server",
    description="Agentic Document Extraction API -- layout-aware, context-threaded extraction for PDF, PPTX, DOCX, HTML",
    version="0.1.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(extract.router, prefix="/api/v1", tags=["extract"])

@app.get("/")
def health():
    return {"status": "ok", "service": "extracta-server"}