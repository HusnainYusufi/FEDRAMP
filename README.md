# FedRAMP Ingestion Core — with Assessment AI Narrative Agent

A backend compliance engine that pulls configuration evidence from AWS accounts and uses an LLM agent
to generate FedRAMP System Security Plan (SSP) narratives from that evidence.

## What problem it solves

FedRAMP authorisation requires a written narrative for every security control, explaining how the
system satisfies it. Doing that by hand takes months and the result is stale as soon as
infrastructure changes. This engine reads the live AWS environment, normalises what it finds, and has
an LLM write control narratives grounded in that evidence.

## How it uses AI

### Agentic narrative generation (LangGraph)

Narrative writing is an explicit state machine rather than a single prompt
(`app/services/ai_agent/narrative/graph.py`):

```
load_control ─▶ analyze_control ─▶ plan_tool_calls ─▶ execute_tools
                                                          │
      parse_output ◀── write_narrative ◀── build_prompt ◀──┴── evaluate_compliance
```

The important design decision is in `plan_tool_calls` / `execute_tools`: the LLM is given **tools to
query PostgreSQL for evidence** rather than having evidence hardcoded per control. Adding controls
needs no new code — the agent determines what to fetch for the control in front of it.

Supporting modules:

- `controls_repo.py` — loads official control text from the `fedramp_controls` table, seeded from the
  FedRAMP High baseline spreadsheet via `scripts/load_fedramp_controls.py`
- `prompt_engine.py` — assembles the prompt from control text plus retrieved evidence
- `output_parser.py` — parses generated Markdown into a structured record for `ssp_narratives`
- `llm_client.py` — `ChatOpenAI` wrapper, default temperature 0.3
- `template_direct.py` — a template-driven path that bypasses the agent for simpler controls

### Policy generation

`ai_agent/policies/` produces compliance policy documents from templates and gathered evidence.

## Evidence ingestion (the non-AI half)

```
AWS Account ──▶ AWS Client ──▶ Normalizer ──▶ PostgreSQL
(SecurityAudit)  (AssumeRole)  (canonical)     (JSONB)
```

`app/services/aws/` assumes a read-only `SecurityAudit` IAM role and harvests IAM, EC2, VPC, S3 and
RDS configuration via boto3. **Raw AWS JSON is never stored** — everything is normalised into
canonical compliance objects first.

## Stack

FastAPI · SQLAlchemy 2.x async + psycopg3 · PostgreSQL · Alembic · LangGraph · LangChain-OpenAI ·
boto3 · structlog · pytest · Docker Compose. Dashboard at `GET /ui/`.

Tests cover the health endpoint, prompt engine, normalizer, output parser, ingest API and
architecture-diagram API.

---

<!-- The original project README is preserved below. -->

# FedRAMP Ingestion Core (with Assessment AI Narrative Agent)

A backend compliance data engine that fetches, normalizes, and stores AWS configuration data (**Use Case 2**) plus an Assessment AI narrative agent that generates FedRAMP SSP narratives from that evidence (**Use Case 1**).

This repository is primarily a **backend service**. A lightweight UI is included for interactive use (see **UI** below).

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌────────────┐     ┌────────────┐
│  AWS Account │────▶│  AWS Client   │────▶│ Normalizer  │────▶│ PostgreSQL │
│ (SecurityAudit)│   │ (AssumeRole)  │     │ (Canonical) │     │  (JSONB)   │
└─────────────┘     └──────────────┘     └────────────┘     └────────────┘
                          │                      │
                          ▼                      ▼
                    Read-only APIs         No raw JSON stored
                    IAM, EC2, VPC,         Everything normalized
                    S3, RDS                into compliance objects
```

Assessment AI (Narrative Agent):

- Loads control text from `fedramp_controls`
- Uses **LLM tools** to dynamically fetch ingested evidence from Postgres (no per-control hardcoding)
- Generates SSP narrative Markdown and stores it in `ssp_narratives`

## UI

FedRAMP AI Studio dashboard:
- `GET /ui/`

## Quick Start

### Prerequisites

- Docker and Docker Compose
- AWS credentials with permission to `sts:AssumeRole`
- A target AWS account with an IAM role that has the **SecurityAudit** managed policy attached

### Run Locally

```bash
# 1. Clone and enter the project
cd fedramp-ingestion-core

# 2. Create your .env (this repo ships env.example; rename it to .env)
cp env.example .env

# 3. Export AWS credentials (or use AWS SSO / instance profiles)
export AWS_ACCESS_KEY_ID=your-key
export AWS_SECRET_ACCESS_KEY=your-secret
export AWS_DEFAULT_REGION=us-east-1
export OPENAI_API_KEY=your-openai-key

# 4. Start everything
docker-compose up --build
```

The API will be available at `http://localhost:8000`.

### Ingest a FedRAMP SSP Template (Template-First)

This repo supports a **template-first** workflow: ingest the FedRAMP SSP template once, store **per-control template Markdown** in Postgres, and then generate narratives by filling that template with evidence.

- **Endpoint**: `POST /templates/ingest`
- **Stores**: per-control Markdown on `fedramp_controls.template_markdown`
- **DOCX parsing backend**: LlamaCloud parsing (LlamaParse) — requires `LLAMA_CLOUD_API_KEY`
- **Optional**: upload pre-parsed Markdown (`.md`) instead of DOCX (no LlamaParse required)

Example (DOCX):

```bash
curl -X POST http://localhost:8000/templates/ingest \
  -F "file=@FedRAMP-SSP-Moderate-Baseline-Template.docx" \
  -F "tag=Rev5"
```

Example (Markdown upload):

```bash
curl -X POST http://localhost:8000/templates/ingest \
  -F "file=@fedramp_template_full.md" \
  -F "tag=Rev5"
```

Browse ingested templates:

- `GET /templates/controls` (list controls with stored template markdown)
- `GET /templates/controls/{control_id}` (fetch a single control’s template markdown)

### Verify It Works

```bash
# Health check
curl http://localhost:8000/health

# Trigger ingestion (replace with your real values)
curl -X POST http://localhost:8000/aws/ingestions \
  -H "Content-Type: application/json" \
  -d '{
    "account_id": "123456789012",
    "role_arn": "arn:aws:iam::123456789012:role/SecurityAudit",
    "regions": ["us-east-1"]
  }'
```

### Load FedRAMP Controls (required for narratives)

The narrative agent reads control definitions from the `fedramp_controls` table. Load them once:

```bash
python scripts/load_fedramp_controls.py
```

### Generate a Narrative (Use Case 1)

- UI: `GET /ui/`
- API: `POST /ai/narratives/generate`

Example:

```bash
curl -X POST http://localhost:8000/ai/narratives/generate \
  -H "Content-Type: application/json" \
  -d '{
    "control_id": "AC-2",
    "account_id": "123456789012",
    "persist": true
  }'
```

### Browse Evidence (Use Case 2)

Evidence endpoints read from Postgres (no live AWS calls):

- `GET /aws/evidence/runs`
- `GET /aws/evidence/runs/{run_id}/summary`
- `GET /aws/evidence/runs/{run_id}/identities`
- `GET /aws/evidence/runs/{run_id}/assets`
- `GET /aws/evidence/runs/{run_id}/network-components`
- `GET /aws/evidence/runs/{run_id}/data-stores`

### Run Without Docker

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start PostgreSQL (locally or via Docker)
docker run -d --name fedramp-db \
  -e POSTGRES_USER=fedramp \
  -e POSTGRES_PASSWORD=fedramp \
  -e POSTGRES_DB=fedramp \
  -p 5432:5432 \
  postgres:16-alpine

# 3. Run migrations
alembic upgrade head

# 4. Load FedRAMP controls (one-time)
python scripts/load_fedramp_controls.py

# 5. Start the server
uvicorn app.main:app --reload
```

### Run Tests

```bash
pip install -r requirements.txt
pytest -v
```

## Environment Variables (what to set)

Source file: `env.example` (rename to `.env`).

Required for **template ingestion (DOCX → Markdown)**:
- `LLAMA_CLOUD_API_KEY`
- Optional: `LLAMA_CLOUD_PARSE_TIMEOUT_SECONDS` (default: 600)

Required for **narrative generation**:
- `OPENAI_API_KEY`
- `OPENAI_MODEL` (default: `gpt-5.2`)

Required for **AWS ingestion** (to call STS AssumeRole):
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_DEFAULT_REGION`
- Optional: `AWS_SESSION_TOKEN` (if using STS/SSO)
- Optional: `AWS_DEFAULT_EXTERNAL_ID` (if your target role requires ExternalId)

DB (defaults are OK for local dev):
- `DATABASE_URL`
- `DATABASE_URL_SYNC`

Assessment AI tool controls (optional):
- `ASSESSMENT_AI_MAX_TOOL_CALLS`
- `ASSESSMENT_AI_DEFAULT_SAMPLE_LIMIT`
- `ASSESSMENT_AI_ENABLED_TOOLS` (comma-separated allowlist; empty = all)

## Template Parsing: “Questions vs. Answers” (Split-Brain)

When the parser encounters a **filled SSP**, it must never let vendor implementation text overwrite the control’s **requirement text**. The internal blueprint parser in `app/services/templates/template_parser.py` enforces this by splitting each control block into two logical zones:

- **Zone A (Definition / Requirements)**: text *before* “Control Summary Information”
- **Zone B (Implementation)**: text *from* “What is the solution and how is it implemented?” onward

The resulting (optional) blueprint JSON shape is:

```json
{
  "control_id": "AC-2",
  "title": "Account Management",
  "summary_table": {
    "responsible_role": "IAM Administrator",
    "parameters": [
      { "id": "ac-2-a", "text": "Assignment: organization-defined frequency", "value": null, "assignment": null }
    ],
    "implementation_status": ["Implemented"],
    "origination": ["Service Provider Corporate"]
  },
  "parts": [
    {
      "id": "a",
      "requirement_text": "The organization identifies and selects ...",
      "parameter_placeholder": "[Assignment: ...]",
      "dragon_implementation": null,
      "inheritance_text": "The system inherits applicable control implementations ...",
      "customer_responsibility": "The customer is responsible for implementing ..."
    }
  ]
}
```

## How AWS AssumeRole Works

This system uses **STS AssumeRole** to access target AWS accounts with read-only permissions:

1. **The target account** creates an IAM role with:
   - The `SecurityAudit` AWS-managed policy (read-only)
   - A trust policy that allows the ingestion service's identity to assume it

2. **This service** calls `sts:AssumeRole` with the target role ARN and receives temporary credentials (valid for 1 hour by default).

3. **All API calls** are made using these temporary credentials. Every call is logged in CloudTrail under the assumed role's session name (`fedramp-ingest-{account_id}`).

4. **No long-lived credentials** are stored by this service.

### Setting Up the Target Role

In the target AWS account, create a role with this trust policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::YOUR_INGESTION_ACCOUNT:root"
      },
      "Action": "sts:AssumeRole",
      "Condition": {
        "StringEquals": {
          "sts:ExternalId": "your-external-id"
        }
      }
    }
  ]
}
```

Attach the `arn:aws:iam::aws:policy/SecurityAudit` managed policy to this role.

## Ingestion Design for FedRAMP

### What Gets Ingested

| AWS Service | Resource Types | Compliance Relevance |
|------------|---------------|---------------------|
| **IAM** | Users, Roles, Policies | AC-2, AC-3, AC-6, IA-2 |
| **EC2** | Instances | CM-2, CM-8, SI-2 |
| **VPC** | VPCs, Subnets, Security Groups | SC-7, AC-4 |
| **S3** | Buckets (metadata) | SC-28, CP-9, AU-2 |
| **RDS** | Instances (metadata) | SC-28, CP-6, CP-9 |

### Normalization Guarantees

- **No raw AWS JSON** is persisted. Every resource is transformed into a canonical compliance object with a typed schema.
- **Deterministic**: The same AWS response always produces the same normalized output.
- **Explainable**: Every normalized field maps directly to a raw AWS field — the mapping is readable in `app/services/normalizer.py`.
- **Auditable**: Every record is tied to an `ingestion_run_id` with timestamps, providing full traceability.

### Data Model

Every compliance record has:

| Column | Purpose |
|--------|---------|
| `id` | UUID primary key (never exposed externally) |
| `resource_id` | Cloud-native identifier (ARN, instance ID, etc.) |
| `account_id` | 12-digit AWS account |
| `region` | AWS region (or "global" for IAM) |
| `resource_type` | Subtype (e.g., `ec2_instance`, `iam_user`) |
| `data` | Normalized attributes as JSONB |
| `ingestion_run_id` | FK to the run that produced this record |
| `created_at` | First ingestion timestamp (UTC) |
| `updated_at` | Last refresh timestamp (UTC) |

## How This Plugs Into Control Mapping

This service is **Phase 1** of a larger system. The normalized data it produces is designed to feed:

### Phase 2 — Control Mapping Engine (not yet built)
- Maps normalized resources to NIST 800-53 controls
- Example: An S3 bucket with `encryption_algorithm: "aws:kms"` satisfies SC-28 (Protection of Information at Rest)
- The JSONB `data` column has typed, queryable fields that make this mapping deterministic

### Phase 3 — Evidence Validator (not yet built)
- Evaluates whether a resource's configuration meets control requirements
- Example: An IAM user with `mfa_enabled: false` fails IA-2(1)
- Uses the normalized `data` fields, never raw API responses

### Phase 4 — AI Narrative Generator (built)
- Uses deterministic ingested evidence from Postgres as ground truth
- Uses an LLM tool layer to dynamically fetch relevant evidence per control (no per-control hardcoding)
- Writes SSP narrative Markdown and persists it with an evidence snapshot for traceability

### Extension Points

- **New AWS services**: Add a method to `AWSIngestor`, a normalizer function, and optionally a new Pydantic schema
- **New cloud providers**: Implement a new client factory and normalizers; the database schema is cloud-agnostic
- **New compliance frameworks**: The data model supports FedRAMP, CMMC, HIPAA, etc. — add framework-specific mapping logic in Phase 2

## Project Structure

```
fedramp-ingestion-core/
├── app/
│   ├── main.py              # FastAPI entry point
│   ├── config.py             # 12-factor configuration
│   ├── logging.py            # Structured JSON logging (AU-2/AU-3)
│   ├── api/
│   │   ├── health.py         # GET /health
│   │   └── ingest.py         # POST /ingest/aws
│   ├── services/
│   │   ├── aws_client.py     # STS AssumeRole client factory
│   │   ├── aws_ingestor.py   # Ingestion orchestrator
│   │   └── normalizer.py     # Raw → canonical transformation
│   ├── models/
│   │   ├── asset.py          # EC2 Pydantic schemas
│   │   ├── identity.py       # IAM Pydantic schemas
│   │   ├── network.py        # VPC/SG Pydantic schemas
│   │   └── datastore.py      # S3/RDS Pydantic schemas
│   └── db/
│       ├── base.py           # SQLAlchemy declarative base
│       ├── session.py         # Async session factory
│       └── models.py          # ORM table definitions
├── alembic/                   # Database migrations
├── tests/                     # Test suite
├── Dockerfile                 # Production container
├── docker-compose.yml         # Local dev stack
└── requirements.txt           # Python dependencies
```

## NIST 800-53 Controls Addressed by This Service

| Control | Family | How This Service Addresses It |
|---------|--------|------------------------------|
| AC-2 | Account Management | Ingests IAM users with MFA status, last login, group membership |
| AC-3 | Access Enforcement | Captures attached policies and inline policies per identity |
| AC-6 | Least Privilege | Records instance profiles, role trust policies |
| AU-2 | Audit Events | Structured JSON logging of every ingestion action |
| AU-3 | Audit Record Content | Every record includes account_id, region, timestamps, run_id |
| CM-2 | Baseline Configuration | Captures EC2 instance types, security groups, VPC configs |
| CM-8 | System Component Inventory | Full resource enumeration across IAM, EC2, VPC, S3, RDS |
| SC-7 | Boundary Protection | Captures VPCs, subnets, security group rules |
| SC-28 | Information at Rest | Records S3/RDS encryption status and KMS key IDs |
| IA-2 | Identification & Authentication | Captures MFA status, access key metadata |
| CP-9 | System Backup | Records RDS backup retention, S3 versioning status |

## License

Proprietary — not for redistribution.
