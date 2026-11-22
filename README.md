# Energia backend

Django REST API and PostgreSQL system of record for the synchronized Energia
mobile and administrator workflows.

## Local synchronized environment

```bash
docker compose up -d postgres
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_demo
.venv/bin/gunicorn energia.wsgi:application --bind 0.0.0.0:8000
```

PostgreSQL is mandatory. Settings deliberately raise an error when
`DATABASE_URL` is absent; there is no backend SQLite fallback. The PostgreSQL
container includes pgvector and migration `core.0001_initial` enables it.

## Demo accounts

| Purpose | Username | Password |
| --- | --- | --- |
| Staff mobile | `maya` | `energia-demo` |
| Django administration | `energia-admin` | `Energia-Admin-Demo-2026!` |

These are validation-only credentials and must be replaced outside the demo.

## Synchronized API

| Resource | Endpoint |
| --- | --- |
| JWT login/refresh | `/api/v1/auth/token/`, `/api/v1/auth/refresh/` |
| Staff profile and activities | `/api/v1/me/work-profile/` |
| Searchable/paginated assets | `/api/v1/assets/` |
| Server QR resolution | `/api/v1/assets/resolve-qr/` |
| Duplicate suggestions | `/api/v1/assets/duplicate-suggestions/` |
| Official equipment assignments | `/api/v1/me/equipment/` |
| Missing-asset review queue | `/api/v1/asset-candidates/` |
| Immutable daily tasks | `/api/v1/me/tasks/` |
| Invoice schemas | `/api/v1/invoice-schemas/` |
| AI/OCR invoice drafts | `/api/v1/invoice-scans/` |
| Mandatory invoice confirmation | `/api/v1/invoice-scans/{id}/confirm/` |
| Official invoices | `/api/v1/invoices/` |
| Generic admin resources | `/api/v1/admin/resources/<resource>/` |
| RAG / knowledge retrieval | `/api/v1/admin/rag/` |
| Rules publish | `/api/v1/admin/rules/{id}/publish/` |
| Charts, flow, health scores | `/api/v1/admin/charts/`, `/flow/`, `/health-scores/` |
| Maintenance, reports, imports | `/api/v1/admin/maintenance/`, `/reports/`, `/imports/` |
| Alerts, recommendations, field scans | `/api/v1/admin/alerts/`, `/recommendations/`, `/field-scans/` |
| Database health | `/api/v1/health/` |

Object queries are scoped through the authenticated user's active organization
and site membership. Profile changes, assignments, candidate submissions, task
generation, and administrator candidate decisions produce append-only audit
events.

Energy habits use approved, versioned `RecommendationTemplate` records. Matching
considers department, activities, verified assets, and interaction types.
Locked `SafetyConstraint` records block conflicting recommendation tags before
a task can be created. Generated exclusions are recorded in append-only audit
metadata, and approved/locked records are read-only in Django Admin.

## Administrator workflow

Open `/admin/`, then use **Asset candidates**. The administrator can approve or
reject selected candidates. Approval creates an official inventory asset and an
assignment for the submitter; the next mobile refresh receives that server
state. Pending candidates never participate in official task matching.

## Verification

```bash
.venv/bin/python manage.py check
.venv/bin/python manage.py test
ENERGIA_API_BASE_URL=https://your-api.example/api/v1 \
  .venv/bin/python scripts/smoke_api.py
```

`scripts/start_demo_tunnel.py` starts an unauthenticated Cloudflare Quick Tunnel
for validation. The current validation base URL is
`https://street-swim-investor-cave.trycloudflare.com/api/v1`. Its
hostname is temporary; production requires an authenticated named tunnel or
another permanent Django runtime behind Cloudflare.

Migration `core.0005_admin_workspace_intelligence` adds the administrator
workspace intelligence records. Knowledge retrieval is citation-grounded keyword
search over approved document chunks with a model-gateway fallback; it does not
require pgvector embeddings.

## AI gateway and evidence worker

The provider seam is `core.services.model_gateway.ModelGateway`. Production is
configured for AgentRouter `gpt-6.1-sol`; the validation environment may
temporarily select Groq `qwen/qwen3.8-27b` with `AI_PROVIDER=groq`. Provider keys
live only in `.env` or deployment secret storage.

Evidence uploads create database-backed `AIJob` records. Run the worker with:

```bash
.venv/bin/python manage.py process_ai_jobs --interval 1
```

Photos receive deterministic format, resolution, and checksum checks before a
model call. Videos are probed with FFprobe and sampled into at most five ordered
JPEG frames with FFmpeg; native video/audio is never sent to the selected model.
Provider failures preserve evidence and route it to human review. Every machine
video result requires human review regardless of model confidence.
