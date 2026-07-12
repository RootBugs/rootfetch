# Rootfetch

Open-source research and content extraction API — a self-hostable alternative to Tavily.

## Features

- **Search** — Web search via SerpAPI, Bing, Google CSE, or DuckDuckGo fallback
- **Extract** — Content extraction from URLs (trafilatura → readability → BeautifulSoup)
- **Crawl** — BFS web crawler with depth/path/domain filters
- **Map** — Link discovery from a starting URL
- **Research** — Multi-step research pipeline: query generation → search → extract → LLM synthesis
- **Rate limiting** — Sliding window with Tavily-compatible error envelopes
- **API key auth** — Key-based authentication with credit tracking
- **Keyless mode** — Limited access without an API key

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Run the server
python run.py
```

The server starts at `http://localhost:8000`.

## API Endpoints

### Health
```
GET /health
```

### Search
```json
POST /search
{
  "query": "latest AI developments",
  "search_depth": "basic",
  "topic": "general",
  "max_results": 5,
  "include_images": false,
  "include_answer": false,
  "include_raw_content": false,
  "include_domains": null,
  "exclude_domains": null
}
```

### Extract
```json
POST /extract
{
  "urls": ["https://example.com/article"],
  "depth": "basic",
  "include_images": false,
  "extract_format": "markdown",
  "query": null,
  "chunks": false
}
```

### Crawl
```json
POST /crawl
{
  "url": "https://example.com",
  "max_depth": 2,
  "max_pages": 50,
  "include_paths": null,
  "exclude_paths": null,
  "include_domains": null,
  "exclude_domains": null,
  "extract_format": "markdown"
}
```

### Map
```json
POST /map
{
  "url": "https://example.com",
  "search": null,
  "include_paths": null,
  "exclude_paths": null,
  "include_domains": null,
  "exclude_domains": null,
  "limit": 100
}
```

### Research
```json
POST /research
{
  "query": "Impact of quantum computing on cryptography",
  "depth": "advanced",
  "include_images": false,
  "include_sources": true,
  "max_results": 5,
  "topic": "general"
}
```

Returns a `request_id`. Poll for results:
```
GET /research/{request_id}
```

## Authentication

Provide your API key via:

- **Authorization header**: `Authorization: Bearer your-api-key`
- **X-Api-Key header**: `X-Api-Key: your-api-key`

Without a key, requests are rate-limited (keyless mode).

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `API_HOST` | `0.0.0.0` | Server bind address |
| `API_PORT` | `8000` | Server port |
| `SECRET_KEY` | `change-me...` | Secret for session/auth |
| `DEBUG` | `true` | Debug mode |
| `DATABASE_URL` | `sqlite+aiosqlite:///./rootfetch.db` | Database connection |
| `DEFAULT_API_KEYS` | `test-key:1000,demo-key:500` | Seed API keys on startup |
| `SERPAPI_API_KEY` | — | SerpAPI key (recommended) |
| `BING_API_KEY` | — | Bing Web Search key |
| `GOOGLE_API_KEY` | — | Google API key (with CSE) |
| `GOOGLE_CSE_ID` | — | Google CSE ID |
| `OPENAI_API_KEY` | — | OpenAI key (for answers/research) |
| `PROXY_URL` | — | HTTP proxy for outgoing requests |
| `KEYLESS_RATE_LIMIT` | `20/minute` | Rate limit for keyless requests |
| `KEYED_RATE_LIMIT` | `100/minute` | Rate limit for keyed requests |

## Search Provider Priority

1. **SerpAPI** — Best quality, recommended
2. **Bing Web Search** — Good quality with Azure subscription
3. **Google CSE** — Requires API key + CSE ID
4. **DuckDuckGo** — No API key needed, but may be blocked

A search API key is required for search to return results.

## Architecture

```
                  ┌─────────────┐
                  │   Client    │
                  └──────┬──────┘
                         │ HTTP
                  ┌──────▼──────┐
                  │  FastAPI    │
                  │  + Auth    │
                  │  + Rate    │
                  │   Limit    │
                  └──┬───┬──┬──┘
        ┌────────────┘   │  └──────────┐
        │                │             │
  ┌─────▼─────┐   ┌──────▼─────┐  ┌───▼────┐
  │  Search   │   │  Extract   │  │ Crawl  │
  │  Provider │   │  (trafil.) │  │ (BFS)  │
  └─────┬─────┘   └──────┬─────┘  └───┬────┘
        │                │             │
  ┌─────▼─────┐   ┌──────▼─────┐  ┌───▼────┐
  │ SerpAPI   │   │ trafilatura│  │  URLs  │
  │ Bing      │   │readability │  │ Pages  │
  │ Google CSE│   │ Beautiful  │  │        │
  │ DuckDuckGo│   │ Soup       │  │        │
  └───────────┘   └────────────┘  └────────┘
```

## Differences from Tavily

- **Open source**: Full source code, self-hosted
- **No vendor lock-in**: Choose your search provider
- **SQLite storage**: No external database required
- **Async throughout**: httpx + asyncio for all I/O
- **Compatible API**: Drop-in replacement for Tavily SDK

## License

MIT
