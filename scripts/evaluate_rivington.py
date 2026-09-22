"""Run the disposable, live Luna evaluation for the Rivington case.

The filled PDF is used only as an oracle of expected target values. It is never
parsed or passed to the evidence or mapping models.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from io import BytesIO
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.agencies import agency_facts
from app.config import get_settings
from app.db import Base
from app.evidence import extract_evidence_model
from app.inspectors import inspect_pdf
from app.mapping import build_model_fill_plan
from app.model_gateway import ModelGateway
from app.models import Artifact, ArtifactKind, ArtifactPurpose, ModelExecution, ProcessingRun
from app.parsers.reducto import ReductoParserAdapter
from app.verification import deterministic_validate

ORACLE = {
    "Trucking RevenueCurrent Year Estimated": "60,000",
    "Col4.16.1": "100",
    "FEIN": "61-2248369",
    "Rivington_InsuredName": "Lift&Shift towing service",
    "Rivington_AgencyName": "JSSR Insurance Agency",
    "Rivington_NoDrivers": "1",
    "TotalUnitsCurrent Year Estimated": "1",
    "DriverCountCurrent Year Estimated": "1",
    "EffectiveDate": "09/10/26",
    "YearEstablished": "2024",
    "PrimaryCityofOps": "Irvine",
    "Rivington_PercentageTrip0": "100",
    "Coverage_AL_YesNo": "/Yes",
    "Coverage_AL_Limit": "300000",
    "Coverage_CARGO_YesNo": "/Yes",
    "Coverage_GL_YesNo": "/Yes",
    "Rivington_GaragingAddress": "15138 spectrum irvine",
    "Rivington_GaragingState": "CA",
    "Rivington_GaragingZip": "92618",
    "Rivington_MailingAddress": "15138 spectrum irvine",
    "CompanyType": "Corporation",
    "Rivington_MailingZip": "92618",
    "Rivington_MailingCity": "Irvine",
    "Rivington_MailingState": "CA",
    "Rivington_GaragingCity": "Irvine",
    "Rivington_MaxHaul": "<50",
    "Rivington_OtherPriceOptions": "Motor Cargo: $100,000 limit; deductible not provided",
}


def _parse_source(path: Path):
    settings = get_settings()
    if not settings.reducto_api_key:
        raise RuntimeError("REDUCTO_API_KEY is required for the live evaluator")
    adapter = ReductoParserAdapter(settings.reducto_api_key, settings.reducto_base_url)
    return adapter.parse(path.name, path.read_bytes())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("tmp/rivington-evaluation.json"))
    args = parser.parse_args()

    source = _parse_source(args.source)
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        source_hash = hashlib.sha256(args.source.read_bytes()).hexdigest()
        artifact = Artifact(
            filename=args.source.name,
            media_type="application/pdf",
            kind=ArtifactKind.PDF,
            purpose=ArtifactPurpose.SOURCE,
            case_key="rivington-evaluation",
            sha256=source_hash,
            size_bytes=args.source.stat().st_size,
            storage_key="evaluation/source.pdf",
        )
        session.add(artifact)
        session.flush()
        run = ProcessingRun(artifact_id=artifact.id, provider="reducto", stage="semantic-extraction")
        session.add(run)
        session.flush()
        snapshot = extract_evidence_model(
            source.blocks,
            "source-evaluation",
            source_hash,
            "reducto",
            source.parser_version,
            session=session,
            run_id=run.id,
        )
        schema = inspect_pdf(BytesIO(args.template.read_bytes()))
        agency = agency_facts(
            {
                "key": "jssr",
                "name": "JSSR Insurance Agency",
                "details": {"legal_name": "JSSR Insurance Agency"},
            }
        )
        gateway = ModelGateway()
        plan = build_model_fill_plan(
            schema,
            [("source-evaluation", snapshot)],
            agency,
            session=session,
            run_id=run.id,
            gateway=gateway,
        )
        validation = deterministic_validate(plan)
        mapping_execution_id = plan.get("model_profile", {}).get("execution_id")
        execution = session.get(ModelExecution, mapping_execution_id) if mapping_execution_id else None
        usage = execution.usage if execution is not None else None

    by_name = {
        target["field"].get("native_full_name") or target["field"].get("native_name"): target
        for target in plan["targets"]
    }
    rows = []
    for name, expected in ORACLE.items():
        target = by_name.get(name)
        candidate = None
        if target and target.get("selected_candidate_id"):
            candidate = next(
                item
                for item in target["candidates"]
                if item["id"] == target["selected_candidate_id"]
            )
        actual = candidate.get("write_value") if candidate else None
        rows.append(
            {
                "field": name,
                "expected_oracle": expected,
                "actual_write_value": actual,
                "canonical_value": candidate.get("canonical_value") if candidate else None,
                "approval_state": candidate.get("approval_state") if candidate else None,
                "status": "match" if actual == expected else "unresolved" if actual is None else "mismatch",
            }
        )
    result = {
        "source": str(args.source),
        "template": str(args.template),
        "facts": len(snapshot["facts"]),
        "accepted_facts": sum(bool(fact.get("accepted")) for fact in snapshot["facts"]),
        "targets": len(plan["targets"]),
        "selected": sum(bool(target.get("selected_candidate_id")) for target in plan["targets"]),
        "auto_approved": sum(
            target.get("approval_state") == "system_approved" for target in plan["targets"]
        ),
        "deterministic_validation": validation,
        "mapping_input_tokens": (usage or {}).get("input_tokens"),
        "mapping_model_calls": 1 if mapping_execution_id else 0,
        "oracle_rows": rows,
        "summary": {
            "matches": sum(row["status"] == "match" for row in rows),
            "unresolved": sum(row["status"] == "unresolved" for row in rows),
            "mismatches": sum(row["status"] == "mismatch" for row in rows),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, default=str))
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
