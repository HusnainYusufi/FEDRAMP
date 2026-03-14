"""
SQLAlchemy ORM models for the compliance data store.

FedRAMP design decisions:
- JSONB `data` column stores the normalized (NOT raw) resource attributes.
  This allows schema evolution without migrations while keeping a typed
  envelope (resource_id, account_id, region, timestamps).
- `resource_type` enables sub-filtering within a table (e.g. "iam_user"
  vs "iam_role" within the identities table).
- `ingestion_run_id` on every record ties it back to the run that
  produced it, satisfying traceability requirements (AU-3, AU-6).
- Tables are designed to be framework-agnostic: the same schema can
  hold data for FedRAMP, CMMC, HIPAA, etc.

Tables:
  assets             — compute resources (EC2 instances, Lambda, etc.)
  identities         — IAM principals (users, roles, policies)
  network_components — VPCs, subnets, security groups, NACLs
  data_stores        — S3 buckets, RDS instances, DynamoDB tables
  ingestion_runs     — audit trail of every ingestion invocation
  fedramp_controls   — NIST 800-53 control definitions (FedRAMP baseline)
  ssp_narratives     — AI-generated SSP narratives tied to controls
  ssp_style_examples — curated narrative tone/style examples (per control)
  control_roadmaps   — control-level roadmap/status overrides for narratives
  documents          — uploaded security docs for evidence search (RAG)
  document_chunks    — chunked doc text + embeddings for vector search
  compliance_findings— ingested scan results + mapped controls
  poam_items         — deviations/exceptions (POA&M) for periodic validation
"""

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, String, Text, Float

from app.db.base import Base, ComplianceRecordMixin
from app.db.types import JSON_TYPE, UUID_TYPE, VECTOR_TYPE

import enum
import uuid
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Ingestion run status
# ---------------------------------------------------------------------------
class RunStatus(str, enum.Enum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Ingestion runs — audit trail
# ---------------------------------------------------------------------------
class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id = Column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    account_id = Column(String(12), nullable=False, index=True)
    regions = Column(JSON_TYPE, nullable=False, comment="List of regions ingested")
    status = Column(
        Enum(
            RunStatus,
            name="run_status",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
        default=RunStatus.RUNNING,
    )
    assets_count = Column(Integer, nullable=False, default=0)
    identities_count = Column(Integer, nullable=False, default=0)
    networks_count = Column(Integer, nullable=False, default=0)
    data_stores_count = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=True)
    started_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    finished_at = Column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Assets — compute resources (EC2, etc.)
# ---------------------------------------------------------------------------
class Asset(ComplianceRecordMixin, Base):
    __tablename__ = "assets"

    resource_type = Column(
        String(50),
        nullable=False,
        index=True,
        comment="Subtype: ec2_instance, lambda_function, etc.",
    )
    data = Column(
        JSON_TYPE,
        nullable=False,
        comment="Normalized asset attributes — never raw API responses",
    )
    ingestion_run_id = Column(
        UUID_TYPE,
        ForeignKey("ingestion_runs.id"),
        nullable=False,
        index=True,
    )


# ---------------------------------------------------------------------------
# Narrative status
# ---------------------------------------------------------------------------
class NarrativeStatus(str, enum.Enum):
    DRAFT = "draft"
    REVIEWED = "reviewed"
    APPROVED = "approved"


# ---------------------------------------------------------------------------
# FedRAMP controls — NIST 800-53 rule definitions
# ---------------------------------------------------------------------------
class FedRAMPControl(Base):
    """
    Stores NIST 800-53 control definitions loaded from the FedRAMP
    High Baseline spreadsheet.  One row per control / enhancement.

    FedRAMP notes:
      PL-2  System Security Plan — the controls catalogue
      SA-11 Developer Security Testing — traceability to source rules
    """

    __tablename__ = "fedramp_controls"

    control_id = Column(
        String(20),
        primary_key=True,
        comment="NIST control identifier, e.g. 'AC-2', 'AC-2 (1)'",
    )
    family = Column(
        String(50),
        nullable=False,
        index=True,
        comment="Control family, e.g. 'ACCESS CONTROL'",
    )
    title = Column(String(255), nullable=False)
    nist_description = Column(Text, nullable=False)
    fedramp_parameters = Column(Text, nullable=True)
    guidance = Column(Text, nullable=True)
    baseline = Column(
        String(20),
        nullable=False,
        server_default="HIGH",
        comment="FedRAMP baseline: LOW, MODERATE, HIGH",
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Parsed FedRAMP narrative template markdown for this control (ingested from DOCX).
    template_markdown = Column(
        Text,
        nullable=True,
        comment="Per-control FedRAMP template markdown (rendered + filled during narrative generation).",
    )


# ---------------------------------------------------------------------------
# SSP Narratives — AI-generated control narratives
# ---------------------------------------------------------------------------
class SSPNarrative(Base):
    """
    Persists each generated SSP narrative draft.

    Audit trail:
      - `evidence_snapshot` captures the exact data the LLM saw,
        satisfying AU-3 (Content of Audit Records).
      - `model` records which LLM version produced the text.
      - `ingestion_run_id` links the narrative back to the AWS scan.
    """

    __tablename__ = "ssp_narratives"

    id = Column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    control_id = Column(
        String(20),
        ForeignKey("fedramp_controls.control_id"),
        nullable=False,
        index=True,
    )
    account_id = Column(String(12), nullable=False, index=True)
    ingestion_run_id = Column(
        UUID_TYPE,
        ForeignKey("ingestion_runs.id"),
        nullable=True,
        index=True,
        comment="The ingestion run whose evidence was used",
    )
    generated_markdown = Column(Text, nullable=False)
    status = Column(
        Enum(
            NarrativeStatus,
            name="narrative_status",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
        default=NarrativeStatus.DRAFT,
    )
    model = Column(
        String(50),
        nullable=False,
        comment="LLM model identifier, e.g. gpt-5.2",
    )
    evidence_snapshot = Column(
        JSON_TYPE,
        nullable=False,
        comment="Deterministic evidence used for this generation (counts + sample rows)",
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Architecture diagrams — AI-generated boundary/dataflow diagrams (Use Case 11)
# ---------------------------------------------------------------------------
class ArchitectureDiagram(Base):
    """
    Persists each generated architecture diagram SVG along with the
    exact evidence and structured spec used to generate it.

    Audit trail:
      - `evidence_json` captures the deterministic ingested evidence used
      - `diagram_spec_json` captures the structured diagram plan
      - `svg_markup` stores the final rendered SVG artifact
    """

    __tablename__ = "architecture_diagrams"

    id = Column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    account_id = Column(String(12), nullable=False, index=True)
    ingestion_run_id = Column(
        UUID_TYPE,
        ForeignKey("ingestion_runs.id"),
        nullable=True,
        index=True,
        comment="The ingestion run whose evidence was used",
    )

    evidence_json = Column(
        JSON_TYPE,
        nullable=False,
        comment="Deterministic diagram evidence derived from ingested resources",
    )
    summarizer_output = Column(
        JSON_TYPE,
        nullable=True,
        comment="Legacy summary payload from older diagram pipelines",
    )
    diagram_spec_json = Column(
        JSON_TYPE,
        nullable=True,
        comment="Structured SVG diagram specification derived from evidence and AI summary",
    )
    svg_markup = Column(
        Text,
        nullable=True,
        comment="Rendered SVG markup for the authorization boundary diagram",
    )
    mermaid_code = Column(
        Text,
        nullable=True,
        comment="Deprecated Mermaid flowchart syntax from previous diagram pipeline",
    )
    mermaid_prompt = Column(
        Text,
        nullable=True,
        comment="Prompt used to generate the Mermaid syntax (audit trail)",
    )
    artist_prompt = Column(Text, nullable=True, comment="Legacy image prompt from older diagram pipelines")

    image_mime_type = Column(String(50), nullable=True, default=None)
    image_base64 = Column(Text, nullable=True, comment="Base64-encoded PNG image payload (deprecated; Mermaid-only mode)")

    model_text = Column(String(50), nullable=False, comment="Text model used for summarization")
    model_mermaid = Column(String(50), nullable=True, comment="Deprecated Mermaid generation model")
    model_image = Column(String(50), nullable=True, comment="Deprecated image generation model")
    renderer_version = Column(String(50), nullable=True, comment="SVG renderer version identifier")

    mermaid_evaluation = Column(
        JSON_TYPE,
        nullable=True,
        comment="Deprecated Mermaid evaluator payload",
    )
    mermaid_score = Column(Integer, nullable=True, comment="Evaluator score 0-100")
    mermaid_iterations = Column(Integer, nullable=True, comment="How many feedback-loop attempts were used")
    diagram_evaluation = Column(
        JSON_TYPE,
        nullable=True,
        comment="Structured evaluation payload for the SVG diagram",
    )
    diagram_score = Column(Integer, nullable=True, comment="SVG diagram evaluation score 0-100")
    diagram_iterations = Column(Integer, nullable=True, comment="How many AI summarization/render attempts were used")

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Identities — IAM principals
# ---------------------------------------------------------------------------
class Identity(ComplianceRecordMixin, Base):
    __tablename__ = "identities"

    resource_type = Column(
        String(50),
        nullable=False,
        index=True,
        comment="Subtype: iam_user, iam_role, iam_policy",
    )
    data = Column(
        JSON_TYPE,
        nullable=False,
        comment="Normalized identity attributes",
    )
    ingestion_run_id = Column(
        UUID_TYPE,
        ForeignKey("ingestion_runs.id"),
        nullable=False,
        index=True,
    )


# ---------------------------------------------------------------------------
# Network components — VPCs, subnets, security groups
# ---------------------------------------------------------------------------
class NetworkComponent(ComplianceRecordMixin, Base):
    __tablename__ = "network_components"

    resource_type = Column(
        String(50),
        nullable=False,
        index=True,
        comment="Subtype: vpc, subnet, security_group",
    )
    data = Column(
        JSON_TYPE,
        nullable=False,
        comment="Normalized network attributes",
    )
    ingestion_run_id = Column(
        UUID_TYPE,
        ForeignKey("ingestion_runs.id"),
        nullable=False,
        index=True,
    )


# ---------------------------------------------------------------------------
# Data stores — S3, RDS, etc.
# ---------------------------------------------------------------------------
class DataStore(ComplianceRecordMixin, Base):
    __tablename__ = "data_stores"

    resource_type = Column(
        String(50),
        nullable=False,
        index=True,
        comment="Subtype: s3_bucket, rds_instance",
    )
    data = Column(
        JSON_TYPE,
        nullable=False,
        comment="Normalized data-store attributes",
    )
    ingestion_run_id = Column(
        UUID_TYPE,
        ForeignKey("ingestion_runs.id"),
        nullable=False,
        index=True,
    )


#
# NOTE: Prior iterations used versioned template tables (fedramp_templates and
# fedramp_control_blueprints) plus a cached JSON blueprint on fedramp_controls.
# The current implementation stores the parsed template Markdown directly on
# each FedRAMPControl row (template_markdown) and generates narratives from that.


# ---------------------------------------------------------------------------
# Documents (RAG) — uploaded security docs, chunked + embedded
# ---------------------------------------------------------------------------
class DocumentStatus(str, enum.Enum):
    UPLOADED = "uploaded"
    PARSED = "parsed"
    EMBEDDED = "embedded"
    FAILED = "failed"


class Document(Base):
    __tablename__ = "documents"

    id = Column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    account_id = Column(
        String(12),
        nullable=True,
        index=True,
        comment="Optional AWS account scoping for tenant isolation; null means global docs",
    )
    doc_type = Column(
        String(50),
        nullable=False,
        index=True,
        comment="policy|procedure|ssp|scan|other (freeform)",
    )
    title = Column(String(255), nullable=False)
    source_path = Column(Text, nullable=True, comment="Original path/name provided at ingest time (citation only)")
    content_sha256 = Column(String(64), nullable=False, index=True, comment="SHA-256 of extracted text")
    status = Column(
        Enum(
            DocumentStatus,
            name="document_status",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
        default=DocumentStatus.UPLOADED,
    )
    # NOTE: `metadata` is reserved on SQLAlchemy Declarative models, so the Python
    # attribute must not be named `metadata`. We keep the *column name* as
    # "metadata" for clarity and portability.
    doc_metadata = Column(
        "metadata",
        JSON_TYPE,
        nullable=False,
        default=dict,
        comment="Arbitrary doc metadata (tags, owners, etc.)",
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id = Column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID_TYPE, ForeignKey("documents.id"), nullable=False, index=True)
    chunk_index = Column(Integer, nullable=False, comment="0-based chunk index within the document")
    text = Column(Text, nullable=False)
    embedding = Column(VECTOR_TYPE, nullable=True, comment="pgvector on Postgres; JSON list elsewhere")
    token_count = Column(Integer, nullable=True)
    page_start = Column(Integer, nullable=True)
    page_end = Column(Integer, nullable=True)
    section = Column(String(255), nullable=True, comment="Optional heading/section path for citations")
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Compliance findings — scan outputs mapped to controls
# ---------------------------------------------------------------------------
class MappingMethod(str, enum.Enum):
    RULE = "rule"
    LLM = "llm"
    MANUAL = "manual"


class ComplianceFinding(Base):
    __tablename__ = "compliance_findings"

    id = Column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    account_id = Column(String(12), nullable=True, index=True)
    source = Column(String(50), nullable=False, index=True, comment="nessus|securityhub|config|other")
    finding_key = Column(String(255), nullable=True, index=True, comment="Scanner-provided stable ID, if any")
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    severity = Column(String(50), nullable=True, index=True)
    resource_id = Column(Text, nullable=True, index=True)
    raw = Column(JSON_TYPE, nullable=False, default=dict, comment="Raw finding payload (redacted if needed)")
    mapped_control_id = Column(String(20), nullable=True, index=True, comment="Best-effort mapped NIST control ID")
    mapping_method = Column(
        Enum(
            MappingMethod,
            name="mapping_method",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=True,
    )
    mapping_confidence = Column(Float, nullable=True, comment="0-1 confidence score")
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# POA&M / deviations — operational exceptions that must be re-validated
# ---------------------------------------------------------------------------
class POAMItem(Base):
    __tablename__ = "poam_items"

    id = Column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    account_id = Column(String(12), nullable=True, index=True)
    item_id = Column(String(100), nullable=True, index=True, comment="POA&M Item ID from spreadsheet/system")
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    cve_ids = Column(JSON_TYPE, nullable=False, default=list, comment="List of CVE IDs if applicable")
    vendor = Column(String(255), nullable=True)
    status = Column(String(50), nullable=True, index=True, comment="Open|Mitigated|Closed|AcceptedRisk|etc.")
    due_date = Column(String(30), nullable=True, comment="Due date (string for cross-dialect portability)")
    last_reviewed_at = Column(DateTime(timezone=True), nullable=True)
    review_notes = Column(Text, nullable=True)
    raw = Column(JSON_TYPE, nullable=False, default=dict, comment="Raw row payload (for audit)")
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# SSP style/tone examples — curated narrative samples per control
# ---------------------------------------------------------------------------
class SSPStyleExample(Base):
    __tablename__ = "ssp_style_examples"

    id = Column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    control_id = Column(
        String(20),
        ForeignKey("fedramp_controls.control_id"),
        nullable=False,
        index=True,
    )
    tone_tier = Column(
        String(20),
        nullable=False,
        index=True,
        comment="low|meduim|high (tone/maturity tier, independent of baseline)",
    )
    example_markdown = Column(
        Text,
        nullable=False,
        comment="Curated example narrative content used as style/tone guidance",
    )
    source_path = Column(
        Text,
        nullable=True,
        comment="Optional: repo-relative path used to seed the example (traceability)",
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Control roadmaps — narrative-safe roadmap/timeline content + Planned overrides
# ---------------------------------------------------------------------------
class ControlRoadmap(Base):
    __tablename__ = "control_roadmaps"

    id = Column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    control_id = Column(
        String(20),
        ForeignKey("fedramp_controls.control_id"),
        nullable=False,
        index=True,
    )
    account_id = Column(
        String(12),
        nullable=True,
        index=True,
        comment="Optional: scope override to a specific AWS account/tenant; null = global",
    )
    status_override = Column(
        String(30),
        nullable=True,
        index=True,
        comment="Optional narrative status override (e.g., Planned)",
    )
    target_date = Column(
        String(30),
        nullable=True,
        comment="Target completion/implementation date (string for cross-dialect portability)",
    )
    milestones = Column(
        JSON_TYPE,
        nullable=False,
        default=list,
        comment="Optional roadmap milestones (JSON list) used to write Planned narratives",
    )
    narrative_roadmap_summary = Column(
        Text,
        nullable=True,
        comment="Narrative-safe roadmap summary (what the SSP writer is allowed to say)",
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
