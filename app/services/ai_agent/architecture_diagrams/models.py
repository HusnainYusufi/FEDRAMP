from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


DiagramFormat = Literal["svg"]
OutputMode = Literal["diagram", "svg"]


@dataclass
class InfraNode:
    """A node to render in a diagram (tool-agnostic)."""

    id: str
    label: str
    kind: str  # e.g. vpc, subnet, igw, nat, ec2, rds, s3, cloudtrail
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class InfraEdge:
    src: str
    dst: str
    label: str = ""


@dataclass
class InfraSpec:
    """Diagram spec derived from evidence (no hallucinations)."""

    account_id: str
    ingestion_run_id: str | None
    title: str = "ABD Overview"
    boundary_label: str = "FedRAMP Authorization Boundary"

    external_services: list[InfraNode] = field(default_factory=list)
    management_path: list[InfraNode] = field(default_factory=list)
    vpcs: list[InfraNode] = field(default_factory=list)
    public_subnets: list[InfraNode] = field(default_factory=list)
    private_subnets: list[InfraNode] = field(default_factory=list)

    perimeter: list[InfraNode] = field(default_factory=list)  # igw/nat/endpoints
    app_tier: list[InfraNode] = field(default_factory=list)  # ec2
    data_tier: list[InfraNode] = field(default_factory=list)  # rds/s3
    security_services: list[InfraNode] = field(default_factory=list)  # cloudtrail/logs/etc

    edges: list[InfraEdge] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    context_summary: dict[str, Any] = field(default_factory=dict)
    legend: list[str] = field(
        default_factory=lambda: [
            "Boundary",
            "Subnet",
            "Component/Service",
            "Data Flow",
            "NOT EVIDENCED / PLANNED",
        ]
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

