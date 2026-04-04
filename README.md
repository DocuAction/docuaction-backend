# DocuAction AI — Backend API

Enterprise Document & Voice Intelligence Platform.

## Architecture

```
app/
├── main.py                    ← FastAPI entry point
├── core/
│   ├── config.py              ← Settings from .env
│   ├── database.py            ← Async PostgreSQL
│   └── security.py            ← JWT auth + bcrypt
├── api/
│   └── routes.py              ← All API endpoints
├── models/
│   ├── database.py            ← SQLAlchemy tables (7 tables)
│   └── schemas.py             ← Pydantic request/response
└── services/
    ├── ai_engine.py           ← Main AI pipeline (routing, fallback, JSON)
    ├── json_repair.py         ← 5-stage JSON extraction + Haiku cleanup
    ├── pii_masking.py         ← 12-pattern PII redaction
    ├── text_chunker.py        ← Long document chunking (20K+ words)
    ├── model_router.py        ← Complexity-based Haiku/Sonnet routing
    ├── audit_logger.py        ← Enterprise audit trail
    └── audio_service.py       ← OpenAI Whisper transcription
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/auth/signup` | Create account |
| POST | `/api/auth/login` | Get JWT token |
| GET | `/api/auth/me` | Current user profile |
| **POST** | **`/api/process`** | **Process text through AI engine** |
| **POST** | **`/api/transcribe`** | **Transcribe audio via Whisper** |
| POST | `/api/documents/upload` | Upload document file |
| GET | `/api/documents` | List documents |
| DELETE | `/api/documents/{id}` | Delete document |
| POST | `/api/outputs/generate/{doc_id}` | Generate AI output from document |
| GET | `/api/outputs` | List AI outputs |
| GET | `/api/outputs/{id}` | Get specific output |
| GET | `/health` | Health check |
| GET | `/docs` | Swagger UI |

## Quick Start (Local)

### 1. Prerequisites
- Python 3.11+
- PostgreSQL 14+

### 2. Setup Database
```bash
createdb docuaction
# Or use Railway/Neon for hosted PostgreSQL
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment
```bash
cp .env.example .env
# Edit .env with your API keys:
#   ANTHROPIC_API_KEY=sk-ant-...
#   OPENAI_API_KEY=sk-...
#   DATABASE_URL=postgresql://user:pass@localhost:5432/docuaction
#   SECRET_KEY=your-random-64-char-string
```

### 5. Run
```bash
uvicorn app.main:app --reload --port 8000
```

### 6. Test
- Health: http://localhost:8000/health
- Swagger: http://localhost:8000/docs
- Sign up: POST to /api/auth/signup
- Process: POST to /api/process with JWT token

## Deploy to Railway

### 1. Push to GitHub
```bash
git init && git add . && git commit -m "DocuAction AI backend"
git remote add origin https://github.com/YOU/docuaction-backend.git
git push -u origin main
```

### 2. Create Railway Project
- Go to railway.app → New Project → Deploy from GitHub
- Select your repo

### 3. Add PostgreSQL
- Click New → Database → PostgreSQL
- Railway auto-creates DATABASE_URL

### 4. Set Environment Variables
```
SECRET_KEY=random-64-chars
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_SONNET_MODEL=claude-sonnet-4-20250514
OPENAI_API_KEY=sk-...
ALLOWED_ORIGINS=https://app.docuaction.io
```

### 5. Add Custom Domain
- Settings → Custom Domain → api.docuaction.io
- Add CNAME in your DNS

### 6. Verify
```
https://api.docuaction.io/health → {"status":"healthy"}
https://api.docuaction.io/docs → Swagger UI
```

## AI Pipeline

```
Request → PII Masking (12 patterns)
       → Context Check (chunk if >20K words)
       → Complexity Router
           → Simple → Haiku ($0.005/doc)
           → Complex → Sonnet ($0.04/doc)
       → AI Generation (15s timeout + retry)
       → JSON Repair (5-stage + Haiku cleanup)
       → Audit Log
       → Structured JSON Response
```

## Cost Estimate

| Component | Monthly Cost |
|-----------|-------------|
| Railway (backend + DB) | $5-10 |
| Claude Haiku (70% of requests) | $1-2 |
| Claude Sonnet (30% of requests) | $1-3 |
| Whisper (audio) | $5-10 |
| **Total** | **$12-25** |

## License

Proprietary — Alliance Global Tech, Inc.
