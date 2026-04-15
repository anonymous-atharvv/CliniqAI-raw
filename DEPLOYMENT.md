# CliniQAI — Deployment Guide

> **Local dev** → **Railway** (backend) + **Netlify** (frontend)

---

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.11+ | python.org |
| Node.js | 20+ | nodejs.org |
| Docker + Docker Compose | 24+ | docker.com |
| Railway CLI | latest | `npm install -g @railway/cli` |
| Netlify CLI | latest | `npm install -g netlify-cli` |

---

## Part 1 — Local Development

### 1. Configure environment

```bash
cp .env.example .env
# Edit .env — set at minimum:
#   JWT_SECRET_KEY=<64-char hex>
#   DEIDENT_SALT=<32-char hex>
#   ANTHROPIC_API_KEY=<your key>

cp frontend/.env.example frontend/.env.local
# VITE_API_BASE_URL=http://localhost:8000  (already set)
```

Generate secure secrets:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

### 2. Start infrastructure

```bash
cd infrastructure && docker compose up -d
# Wait 15s then check: docker compose ps  (all "healthy")
```

Services: PostgreSQL:5432, Redis:6379, Kafka:9092, Qdrant:6333, Grafana:3001

### 3. Migrate database

```bash
python3 scripts/migrate_db.py
```

### 4. Start backend

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Visit: http://localhost:8000/docs (Swagger UI)

### 5. Start frontend

```bash
cd frontend
npm install && npm run dev
```

Visit: http://localhost:3000

### 6. Run tests

```bash
python3 -m pytest tests/unit/ tests/clinical/ -v
# Expected: 153/153 passed
```

---

## Part 2 — Deploy Backend to Railway

### Step 1 — Create project

```bash
railway login
railway init   # Empty project → name "cliniqai-backend"
```

### Step 2 — Add database plugins

In Railway dashboard → your project → **+ New**:
- **Database → PostgreSQL** (auto-sets `DATABASE_URL`)
- **Database → Redis**      (auto-sets `REDIS_URL`)

### Step 3 — Set environment variables

In Railway dashboard → your service → **Variables**:

```
ENVIRONMENT=production
JWT_SECRET_KEY=<your 64-char secret>
DEIDENT_SALT=<your 32-char salt>
ANTHROPIC_API_KEY=<your key>
HOSPITAL_ID=your_hospital_001
HOSPITAL_NAME=Your Hospital Name
ALLOWED_ORIGINS=["https://your-site.netlify.app","http://localhost:3000"]
KAFKA_ENABLED=false
FEATURE_SEPSIS_PREDICTION=true
FEATURE_PHARMACIST_AGENT=true
FEATURE_IMAGING_AI=false
FDA_CLEARANCE_STATUS=pending_510k
```

> DATABASE_URL and REDIS_URL are auto-injected by Railway plugins.

### Step 4 — Deploy

```bash
# From repo root
railway up
```

Or connect GitHub in Railway dashboard for auto-deploy on push.

### Step 5 — Migrate database on Railway

```bash
railway run python3 scripts/migrate_db.py
```

### Step 6 — Verify

```bash
curl https://your-app.up.railway.app/health
# {"status":"healthy","version":"1.0.0","environment":"production"}
```

---

## Part 3 — Deploy Frontend to Netlify

### Step 1 — Update netlify.toml

Edit `netlify.toml` — replace both redirect targets:

```toml
[[redirects]]
  from = "/api/*"
  to   = "https://YOUR-RAILWAY-APP.up.railway.app/api/:splat"

[[redirects]]
  from = "/auth/*"
  to   = "https://YOUR-RAILWAY-APP.up.railway.app/auth/:splat"
```

### Step 2 — Deploy

```bash
netlify login
cd frontend && npm run build
netlify deploy --prod --dir=dist
```

Or connect GitHub in Netlify dashboard:
- Base directory: `frontend`
- Build command: `npm install && npm run build`
- Publish directory: `frontend/dist`

### Step 3 — Set Netlify environment variables

In Netlify dashboard → Site settings → Environment variables:

```
VITE_API_BASE_URL = https://your-app.up.railway.app
VITE_WS_URL       = wss://your-app.up.railway.app
VITE_HOSPITAL_NAME = Your Hospital Name
VITE_HOSPITAL_ID  = your_hospital_001
```

### Step 4 — Trigger redeploy

In Netlify dashboard → Deploys → **Trigger deploy** (needed after env var changes).

### Step 5 — Verify

```bash
curl https://your-site.netlify.app          # Returns HTML
curl https://your-site.netlify.app/health   # Returns {"status":"healthy"}
```

---

## Environment Variables Reference

### Backend (.env / Railway)

| Variable | Required | Description |
|----------|----------|-------------|
| `ENVIRONMENT` | ✅ | `development` or `production` |
| `JWT_SECRET_KEY` | ✅ | 64-char random hex |
| `DEIDENT_SALT` | ✅ | 32-char random hex for PHI pseudonymisation |
| `ANTHROPIC_API_KEY` | ✅ | Anthropic API key |
| `HOSPITAL_ID` | ✅ | Unique hospital slug |
| `HOSPITAL_NAME` | ✅ | Display name |
| `ALLOWED_ORIGINS` | ✅ | JSON array of allowed frontend URLs |
| `DATABASE_URL` | ✅* | Auto-set by Railway PostgreSQL plugin |
| `REDIS_URL` | ✅* | Auto-set by Railway Redis plugin |
| `KAFKA_ENABLED` | ❌ | `false` for MVP (no broker needed) |
| `FEATURE_SEPSIS_PREDICTION` | ❌ | `true` |
| `FEATURE_IMAGING_AI` | ❌ | `false` (needs GPU) |
| `FEATURE_PHARMACIST_AGENT` | ❌ | `true` |

### Frontend (.env.local / Netlify)

| Variable | Required | Description |
|----------|----------|-------------|
| `VITE_API_BASE_URL` | ✅ | Backend URL (`https://` in production) |
| `VITE_WS_URL` | ✅ | WebSocket URL (`wss://` in production) |
| `VITE_HOSPITAL_NAME` | ❌ | Shown in sidebar |
| `VITE_HOSPITAL_ID` | ❌ | Sent with API requests |

---

## Troubleshooting

**Railway: App won't start**
```bash
railway logs --tail 50
# Common: JWT_SECRET_KEY not set, DATABASE_URL missing
```

**Netlify: "Page not found" on refresh**
→ Check `netlify.toml` has `from = "/*"` redirect with `status = 200`

**CORS errors in browser**
→ Add your Netlify domain to Railway's `ALLOWED_ORIGINS` env var → redeploy

**WebSocket not connecting**
→ Set `VITE_WS_URL=wss://your-app.up.railway.app` (not via Netlify proxy)
→ Railway allows WebSocket natively

**Imports fail on Railway**
```bash
railway run python3 -c "import sys; sys.path.insert(0,'backend'); import main; print('OK')"
```

---

## Quick Reference: URLs

| | Local | Production |
|-|-------|------------|
| **Frontend** | `http://localhost:3000` | `https://your-site.netlify.app` |
| **Backend API** | `http://localhost:8000` | `https://your-app.up.railway.app` |
| **API Docs** | `http://localhost:8000/docs` | *disabled in production* |
| **Health** | `http://localhost:8000/health` | `https://your-app.up.railway.app/health` |
| **Grafana** | `http://localhost:3001` | self-hosted only |
