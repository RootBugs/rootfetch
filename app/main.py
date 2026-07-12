"""FastAPI application setup for Rootfetch."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import app.compat  # noqa: F401 - Python 3.14 compatibility

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db
from app.models import HealthResponse
from app.rate_limit import RateLimitMiddleware

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: initialize database on startup."""
    logger.info("Starting Rootfetch server")
    await init_db()
    logger.info("Database initialized")
    yield
    logger.info("Shutting down Rootfetch server")


app = FastAPI(
    title="Rootfetch API",
    description="Open-source research and content extraction API. "
    "Multi-engine scraper with TF-IDF reranking, answer synthesis. No paid APIs.",
    version="1.1.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting middleware
app.add_middleware(RateLimitMiddleware)


# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch unhandled exceptions and return a structured error response."""
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "An internal server error occurred.",
            "error": "Internal server error",
        },
    )


# Health endpoint
@app.get("/health", response_model=HealthResponse, tags=["health"])
async def health():
    """Health check endpoint."""
    return HealthResponse(
        status="ok",
        version="1.1.0",
        service="rootfetch",
    )


# Import and include routers
from app.routers import search, extract, crawl, map, research

app.include_router(search.router)
app.include_router(extract.router)
app.include_router(crawl.router)
app.include_router(map.router)
app.include_router(research.router)


# Branded Swagger UI
_SWAGGER_CSS = """
:root {
  --bg: #0c0f1a;
  --surface: #13182b;
  --border: #1e2648;
  --accent: #00d4aa;
  --text: #8892b0;
  --text-bright: #e2e8f0;
}
body { background: var(--bg); }
.swagger-ui { color: var(--text); }
.swagger-ui .topbar { background: var(--surface) !important; border-bottom: 1px solid var(--border); }
.swagger-ui .topbar .download-url-wrapper .select-label { color: var(--text); }
.swagger-ui .info .title { color: var(--text-bright); }
.swagger-ui .info { margin: 30px 0; }
.swagger-ui .info p, .swagger-ui .info li, .swagger-ui .info table { color: var(--text); }
.swagger-ui .info a { color: var(--accent); }
.swagger-ui .btn.authorize { border-color: var(--accent); color: var(--accent); }
.swagger-ui .btn.authorize svg { fill: var(--accent); }
.swagger-ui .opblock-tag { color: var(--text-bright); border-bottom: 1px solid var(--border); }
.swagger-ui .opblock-tag:hover { background: rgba(0,212,170,0.03); }
.swagger-ui .opblock .opblock-summary-method { border-radius: 6px; font-size: 12px; font-weight: 700; }
.swagger-ui .opblock.opblock-get .opblock-summary-method { background: #0b4b6b; }
.swagger-ui .opblock.opblock-post .opblock-summary-method { background: #0d5e3a; }
.swagger-ui .opblock.opblock-delete .opblock-summary-method { background: #6b2020; }
.swagger-ui .opblock.opblock-patch .opblock-summary-method { background: #5e4a0b; }
.swagger-ui .opblock.opblock-put .opblock-summary-method { background: #7a4a0b; }
.swagger-ui .opblock { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; }
.swagger-ui .opblock .opblock-summary { border-bottom: 1px solid var(--border); }
.swagger-ui .opblock .opblock-section-header { background: rgba(0,212,170,0.04); border-bottom: 1px solid var(--border); }
.swagger-ui .opblock .opblock-section-header h4 { color: var(--text-bright); }
.swagger-ui .opblock .opblock-body .opblock-description-wrapper p { color: var(--text); }
.swagger-ui .opblock .opblock-body .parameters-col_name, .swagger-ui .opblock .opblock-body .parameters-col_description { color: var(--text); }
.swagger-ui table thead tr td, .swagger-ui table thead tr th { color: var(--text-dim); border-bottom: 1px solid var(--border); }
.swagger-ui .parameter__name { color: var(--text-bright); }
.swagger-ui .model-box { background: var(--bg); border-radius: 8px; }
.swagger-ui .model { color: var(--text); }
.swagger-ui select, .swagger-ui input[type=text] { background: var(--bg); color: var(--text); border: 1px solid var(--border); border-radius: 6px; }
.swagger-ui select:focus, .swagger-ui input[type=text]:focus { border-color: var(--accent); outline: none; }
.swagger-ui .response-col_status { color: var(--text-bright); }
.swagger-ui .response-col_description { color: var(--text); }
.swagger-ui .responses-inner h4, .swagger-ui .responses-inner h5 { color: var(--text-bright); }
.swagger-ui .markdown p, .swagger-ui .markdown li { color: var(--text); }
.swagger-ui .loading-container .loading::after { color: var(--text-dim); }
.swagger-ui .model-title { color: var(--text-bright); }
.swagger-ui section.models { border: 1px solid var(--border); border-radius: 10px; }
.swagger-ui section.models.is-open h4 { border-bottom: 1px solid var(--border); }
.swagger-ui .model-container { background: var(--surface); border-radius: 8px; }
.swagger-ui .prop-type { color: var(--accent); }
code { color: var(--accent); }
.swagger-ui .scheme-container { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; box-shadow: none; }
::selection { background: rgba(0,212,170,0.3); color: #fff; }
"""


@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Rootfetch API — Docs</title>
  <link rel="stylesheet" href="/static/swagger/swagger-ui.css">
  <style>{_SWAGGER_CSS}</style>
  <style>
    .rt-header {{ display: flex; align-items: center; justify-content: space-between; padding: 14px 40px; background: #13182b; border-bottom: 1px solid #1e2648; }}
    .rt-header-brand {{ display: flex; align-items: center; gap: 10px; color: #e2e8f0; font-family: 'Space Grotesk', sans-serif; font-size: 1.1rem; font-weight: 700; text-decoration: none; }}
    .rt-header-brand .accent {{ color: #00d4aa; }}
    .rt-header a {{ color: #8892b0; font-size: 0.82rem; text-decoration: none; }}
    .rt-header a:hover {{ color: #00d4aa; }}
    .rt-header .version {{ color: #5c6b8a; font-size: 0.75rem; font-family: 'JetBrains Mono', monospace; }}
    .swagger-ui .topbar {{ display: none !important; }}
  </style>
</head>
<body>
  <div class="rt-header">
    <a class="rt-header-brand" href="/"><span class="accent">root</span>fetch <span class="version">v1.1</span></a>
    <a href="/">Back to UI &rarr;</a>
  </div>
  <div id="swagger-ui"></div>
  <script src="/static/swagger/swagger-ui-bundle.js"></script>
  <script>
    SwaggerUIBundle({{
      url: "/openapi.json",
      dom_id: "#swagger-ui",
      deepLinking: true,
      docExpansion: "list",
      defaultModelsExpandDepth: -1,
      syntaxHighlight: {{ activate: true, theme: "monokai" }},
    }});
  </script>
</body>
</html>""")



# Static UI — mounted last so API routes take priority
_static_dir = Path(__file__).parent.parent / "static"
if _static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


@app.get("/", include_in_schema=False)
async def serve_ui():
    ui_path = _static_dir / "index.html"
    if ui_path.is_file():
        return FileResponse(str(ui_path))
    return JSONResponse({"message": "Rootfetch API — visit /docs for docs"})
