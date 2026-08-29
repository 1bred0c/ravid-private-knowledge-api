# RAVID Private Knowledge API

Django REST backend for a subscription-based private knowledge chatbot. Users
authenticate with JWT, subscribe to a plan, upload PDF/TXT/Markdown documents,
and query one or more of their indexed documents through standard RAG or the
optional HyDE retrieval pipeline.

Companion frontend: [ravid-private-knowledge-fe](https://github.com/1bred0c/ravid-private-knowledge-fe). Start this backend stack first, then follow the frontend repository's Docker instructions and open http://localhost:5173.

## Architecture

```text
Client
  -> Django REST + Gunicorn
       -> PostgreSQL + pgvector  (application data, chat history, vectors)
       -> Redis                  (Celery broker/result backend, daily token quota)
       -> Celery                 (asynchronous document ingestion)
       -> OpenRouter             (embeddings and chat models)
       -> VNPay                  (subscription payments)

Flower -> Celery monitoring dashboard
```

The Docker Compose stack contains every service requested by the assessment:

| Service | Purpose | Port |
| --- | --- | --- |
| `web` | Django served by Gunicorn | `8000` |
| `db` | PostgreSQL 16 with pgvector | `5432` |
| `redis` | Broker, result backend, atomic token quota | `6379` |
| `celery` | Document ingestion worker | internal |
| `flower` | Celery monitoring dashboard | `5555` |

## Features

- Custom Django user and JWT register/login/refresh/me APIs.
- Subscription plans, daily token/document/file-size limits, and Redis-backed
  atomic usage reservation.
- VNPay signed payment URL, return, and idempotent IPN processing.
- Multipart PDF, TXT, and Markdown upload with asynchronous Celery ingestion.
- LangChain `RecursiveCharacterTextSplitter`, OpenRouter embeddings, and
  user-isolated LangChain PGVector collections.
- Task status polling with `PROCESSING`, `SUCCESS`, and `FAILURE` states.
- Conversations and PostgreSQL-backed message history.
- RAG over one or many documents explicitly selected by their owner.
- Optional HyDE through `use_hyde`, including hypothetical-passage metadata and
  automatic fallback to standard retrieval on timeout/provider failure.
- OpenAPI schema and interactive Swagger UI.

## Quick start with Docker Compose

### 1. Configure environment

```powershell
Copy-Item .env.example .env
```

At minimum, edit `.env` and set:

```env
DJANGO_SECRET_KEY=replace-with-a-long-random-secret
OPENROUTER_API_KEY=your-openrouter-api-key
```

VNPay is optional for document/chat testing. To test paid subscriptions, also
set:

```env
VNPAY_TMN_CODE=your-terminal-code
VNPAY_HASH_SECRET=your-hash-secret
VNPAY_RETURN_URL=http://127.0.0.1:8000/api/payments/vnpay/return/
VNPAY_IPN_URL=https://your-public-domain.example/api/payments/vnpay/ipn/
```

Do not commit `.env`. It is excluded by `.gitignore` and `.dockerignore`.

### 2. Build and start the complete stack

```powershell
docker compose up --build -d
docker compose ps
```

The web container waits for PostgreSQL and Redis, applies Django migrations,
collects static assets, and then starts Gunicorn. The pgvector extension is
enabled by the document migration.

Follow logs when troubleshooting:

```powershell
docker compose logs -f web celery
```

Create an admin account:

```powershell
docker compose exec web python manage.py createsuperuser
```

### 3. Open the application

- Swagger UI: http://127.0.0.1:8000/api/docs/
- OpenAPI YAML/JSON: http://127.0.0.1:8000/api/schema/
- Health check: http://127.0.0.1:8000/api/health/
- Django admin: http://127.0.0.1:8000/admin/
- Flower dashboard: http://127.0.0.1:5555/

Stop containers while keeping data:

```powershell
docker compose down
```

Delete containers and local Docker volumes only when a complete reset is
intended:

```powershell
docker compose down --volumes
```

## Full end-to-end test with Swagger

Open http://127.0.0.1:8000/api/docs/. Expand an operation, click **Try it
out**, provide the request, and click **Execute**. Swagger uses the generated
OpenAPI schema, including the multipart file picker and all request fields.

After login, copy the `access` value, click **Authorize** at the top of Swagger,
and paste the JWT value only. Swagger automatically sends:

```http
Authorization: Bearer <access-token>
```

The following order tests the complete application workflow.

### 1. Register and login

```http
POST /api/auth/register/
```

```json
{
  "username": "candidate",
  "email": "candidate@example.com",
  "firstName": "Back",
  "lastName": "End",
  "password": "strong-password"
}
```

```http
POST /api/auth/login/
```

```json
{
  "username": "candidate",
  "password": "strong-password"
}
```

Expected: HTTP `200` with `access` and `refresh`. Authorize Swagger before
continuing. Verify the identity with `GET /api/auth/me/`.

### 2. Subscribe

List active plans and subscribe to one:

```http
GET  /api/subscription-plans/
POST /api/subscriptions/subscribe/
```

```json
{
  "planId": "<plan-uuid>"
}
```

The seeded `FREE` plan activates immediately. A paid plan stays `PENDING` until
a verified VNPay callback succeeds.

For a complete payment test, select the seeded `PRO` plan on a fresh account.
Expected subscription status before payment: `PENDING`.

#### VNPay sandbox payment test

After subscribing to a paid plan, call `POST /api/payments/vnpay/create/`. Open
the returned `paymentUrl` and use VNPay's NCB sandbox card:

```json
{
  "subscriptionId": "<pending-subscription-uuid>"
}
```

| Field | Test value |
| --- | --- |
| Bank | NCB |
| Card number | `9704198526191432198` |
| Cardholder | `NGUYEN VAN A` |
| Issue date | `07/15` |
| OTP | `123456` |

For localhost demonstration, `VNPAY_PROCESS_RETURN=True` processes the verified
browser return because VNPay cannot send an IPN to a private localhost URL. In
production, set it to `False` and use the server-to-server IPN as authority.
After returning from VNPay, execute `GET /api/subscriptions/me/`. Expected
subscription status: `ACTIVE`.

### 3. Upload and process a document

```http
POST /api/documents/upload/
Content-Type: multipart/form-data
```

Form fields:

```text
file:  @knowledge-base.pdf
title: Optional display title
```

Successful response (`202 Accepted`):

```json
{
  "message": "Document uploaded and ingestion started",
  "document_id": "<document-uuid>",
  "task_id": "<celery-task-id>"
}
```

Poll ingestion:

```http
GET /api/documents/status/?task_id=<celery-task-id>
```

Expected running response:

```json
{
  "task_id": "<celery-task-id>",
  "status": "PROCESSING"
}
```

Expected final response:

```json
{
  "task_id": "<celery-task-id>",
  "status": "SUCCESS",
  "message": "Document successfully parsed, embedded, and indexed in vector storage."
}
```

Do not query the document until the status is `SUCCESS` and its document state
is `READY`. A failed document can be reprocessed without re-uploading:

```http
POST /api/documents/<document-uuid>/retry/
```

### 4. Create a conversation

```http
POST /api/chat/conversations/
```

```json
{
  "title": "Employee handbook"
}
```

List the current user's conversations:

```http
GET /api/chat/conversations/
```

### 5. Query selected documents

Standard RAG (`use_hyde` defaults to `false`):

```http
POST /api/chat/query/
```

```json
{
  "conversation_id": "<conversation-uuid>",
  "document_ids": ["<ready-document-uuid>"],
  "query": "What is the cancellation policy?",
  "use_hyde": false
}
```

HyDE retrieval:

```json
{
  "conversation_id": "<conversation-uuid>",
  "document_ids": [
    "<ready-document-uuid-1>",
    "<ready-document-uuid-2>"
  ],
  "query": "What is the cancellation policy?",
  "use_hyde": true
}
```

HyDE generates a hypothetical ideal passage and uses its embedding only for
retrieval. The final answer is grounded in the real retrieved chunks and the
original question. `retrieval_metadata` returns the mode, hypothetical passage,
real source chunks, and fallback information for grading visibility.

Read saved conversation history:

```http
GET /api/chat/history/
```

Final checks in Swagger:

1. `GET /api/chat/conversations/` contains the created conversation and an
   increased `message_count`.
2. `GET /api/chat/history/` contains both `USER` and `ASSISTANT` messages.
3. `GET /api/subscriptions/me/` shows increased `tokensUsedToday` and decreased
   `tokensRemainingToday`.
4. Standard RAG returns `retrieval_metadata.mode = standard`.
5. HyDE returns `retrieval_metadata.mode = hyde`, a non-null
   `hypothetical_passage`, and real `source_chunks` from only the selected
   documents.

### Negative and security checks

- Upload an unsupported extension and expect HTTP `400`.
- Query a document still processing and expect HTTP `404`.
- Submit chat without an active subscription and expect HTTP `403`.
- Submit an empty `document_ids` list and expect HTTP `400`.
- Use another user's conversation/document UUID and expect HTTP `404`.
- Stop Redis and submit chat; expect HTTP `503` rather than an unhandled error.

## API documentation

- Interactive Swagger UI: http://127.0.0.1:8000/api/docs/
- Runtime OpenAPI schema: http://127.0.0.1:8000/api/schema/
- Versioned generated schema: [`openapi.yaml`](openapi.yaml)

Regenerate and validate the checked-in schema after API changes:

```powershell
.\.venv\Scripts\python.exe manage.py spectacular --file openapi.yaml --validate
```

## Important API endpoints

| Area | Method | Endpoint |
| --- | --- | --- |
| Auth | POST | `/api/auth/register/` |
| Auth | POST | `/api/auth/login/` |
| Auth | POST | `/api/auth/token/refresh/` |
| Auth | GET | `/api/auth/me/` |
| Plans | GET | `/api/subscription-plans/` |
| Subscription | POST | `/api/subscriptions/subscribe/` |
| Subscription | GET | `/api/subscriptions/me/` |
| VNPay | POST | `/api/payments/vnpay/create/` |
| VNPay | GET | `/api/payments/vnpay/return/` |
| VNPay | GET | `/api/payments/vnpay/ipn/` |
| Documents | POST | `/api/documents/upload/` |
| Documents | GET | `/api/documents/status/?task_id=...` |
| Documents | GET | `/api/documents/` |
| Conversations | POST/GET | `/api/chat/conversations/` |
| Chat | POST | `/api/chat/query/` |
| Chat | GET | `/api/chat/history/` |

The generated [OpenAPI specification](openapi.yaml) contains the complete
request and response schemas.

## Configuration reference

| Variable | Purpose | Default |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | Django signing secret | required for deployment |
| `DJANGO_DEBUG` | Debug mode | `True` in example |
| `DATABASE_URL` | Host-run PostgreSQL/Neon URL | local PostgreSQL |
| `POSTGRES_*` | Docker PostgreSQL database/user/password | `ravid` |
| `REDIS_URL` | Redis broker and usage store | local Redis |
| `OPENROUTER_API_KEY` | Embeddings and chat access | required for RAG |
| `OPENROUTER_EMBEDDING_MODEL` | Document/query embedding model | `openai/text-embedding-3-small` |
| `OPENROUTER_CHAT_MODEL` | Final-answer model/router | `openrouter/free` |
| `OPENROUTER_HYDE_MODEL` | Hypothetical-passage model/router | `openrouter/free` |
| `RAG_CHUNK_SIZE` | LangChain chunk size in characters | `1000` |
| `RAG_CHUNK_OVERLAP` | Chunk overlap in characters | `200` |
| `RAG_RETRIEVAL_K` | Maximum retrieved chunks | `5` |
| `HYDE_TIMEOUT_SECONDS` | HyDE generation timeout before fallback | `20` |
| `VNPAY_PROCESS_RETURN` | Process signed browser return in local development | `True` |

The Compose stack overrides `DATABASE_URL`, `LANGCHAIN_PG_CONNECTION`, and
`REDIS_URL` with internal service addresses. A host-run development server can
instead use Neon by setting `DATABASE_URL` to a Neon PostgreSQL URL.

## Local development without Dockerized web/worker

Docker can provide only PostgreSQL and Redis while Django and Celery run on the
host:

```powershell
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
docker compose up -d db redis
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py runserver
```

In a second PowerShell terminal, use Celery's solo pool on Windows:

```powershell
.\.venv\Scripts\celery.exe -A config worker --loglevel=info --pool=solo
```

## Submission and environment delivery

Commit `.env.example`, never `.env`. If reviewers need a ready-to-run sandbox
configuration, send the real `.env` separately from the GitHub repository (for
example as an encrypted email attachment) and send its password through a
different channel. The reviewer can then clone the repository, place `.env` in
the project root, and run `docker compose up --build -d`.

Rotate temporary Neon, OpenRouter, and VNPay credentials after the evaluation.

## Verification

Run Django checks, migrations check, tests, and OpenAPI validation:

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --noinput
.\.venv\Scripts\python.exe manage.py test
.\.venv\Scripts\python.exe manage.py spectacular --file openapi.yaml --validate
docker compose config --quiet
```

For production, use unique database credentials, `DJANGO_DEBUG=False`, a strong
secret key, HTTPS callback URLs, restricted allowed hosts, protected Flower
access, and a managed media/object-storage strategy.
