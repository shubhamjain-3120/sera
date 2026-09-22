import hashlib
import json
import time
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agencies import AGENCY_SOURCE_VERSION, SEED_AGENCIES, agency_facts
from app.config import get_settings
from app.evidence import extract_evidence, extract_evidence_model, native_parse
from app.evidence.retrieve import candidate_pools
from app.inspectors import inspect_pdf, inspect_xlsx
from app.mapping import (
    AGENCY_SNAPSHOT_ID,
    MAPPER_VERSION,
    MAPPING_PROMPT_VERSION,
    MAPPING_SCHEMA_VERSION,
    MODEL_MAPPER_VERSION,
    apply_revision,
    build_fill_plan,
    build_model_fill_plan,
    mapping_profile_hash,
)
from app.model_gateway import ModelGateway
from app.models import (
    Agency,
    Artifact,
    EvidenceBundle,
    EvidenceSnapshot,
    FillPlan,
    FillPlanRevision,
    ProcessingRun,
    ReviewDecision,
    RunStatus,
    TemplateDraft,
    TemplateVersion,
    new_id,
)
from app.parsers.reducto import ReductoParserAdapter
from app.review import apply_review_decision
from app.schemas import TemplateSchema
from app.storage import ObjectStorage
from app.verification import deterministic_validate


class RevisionConflict(Exception):
    pass


def inspect_artifact(session: Session, storage: ObjectStorage, run_id: str) -> None:
    run = session.get(ProcessingRun, run_id)
    if not run:
        raise KeyError(run_id)
    run.status = RunStatus.RUNNING
    run.started_at = datetime.now(UTC)
    run.progress = 10
    session.commit()
    try:
        with storage.open(run.artifact.storage_key) as source:
            content = source.read()
        if run.artifact.kind.value == "pdf":
            provider_blocks = None
            provider_error = None
            settings = get_settings()
            if settings.reducto_api_key:
                try:
                    parsed = ReductoParserAdapter(
                        settings.reducto_api_key, settings.reducto_base_url
                    ).parse(run.artifact.filename, content)
                    provider_blocks = parsed.blocks
                    run.provider = "reducto+native"
                    run.provider_job_id = parsed.provider_job_id
                    run.parser_version = parsed.parser_version
                    run.raw_result = parsed.raw
                    run.config_snapshot = {
                        "provider": "reducto",
                        "layout_reconciliation": "geometry-v1",
                    }
                except Exception as exc:
                    provider_error = f"{type(exc).__name__}: {exc}"
            schema = inspect_pdf(BytesIO(content), provider_blocks)
            if provider_error:
                schema["inspection"]["warnings"].append(
                    f"Reducto enrichment failed; native layout was used: {provider_error}"
                )
        else:
            schema = inspect_xlsx(BytesIO(content))
        run.progress = 90
        run.result = schema
        draft = session.scalar(select(TemplateDraft).where(TemplateDraft.artifact_id == run.artifact_id))
        if draft:
            draft.schema = _reconcile_inspection_annotations(draft.schema, schema)
            draft.revision += 1
        else:
            draft = TemplateDraft(
                artifact_id=run.artifact_id,
                name=Path(run.artifact.filename).stem,
                schema=schema,
            )
            session.add(draft)
        run.status = RunStatus.SUCCEEDED
        run.progress = 100
        run.finished_at = datetime.now(UTC)
        session.commit()
    except Exception as exc:
        session.rollback()
        run = session.get(ProcessingRun, run_id)
        if run:
            run.status = RunStatus.FAILED
            run.error = f"{type(exc).__name__}: {exc}"
            run.finished_at = datetime.now(UTC)
            session.commit()
        raise


def _reconcile_inspection_annotations(
    previous_schema: dict, inspected_schema: dict
) -> dict:
    """Carry only explicit human annotations onto a fresh native inspection.

    Geometry, widget identities, choices, and PDF constraints always come from
    the new inspection. Semantic and label values inferred by an older inspector
    are intentionally not copied because they may be stale after a parser fix.
    """
    result = dict(inspected_schema)
    new_fields = [dict(field) for field in inspected_schema.get("fields", [])]
    prior_by_identity: dict[str, dict] = {}
    for field in previous_schema.get("fields", []):
        identities = [field.get("native_full_name"), field.get("native_object_id"), field.get("id")]
        for identity in identities:
            if identity:
                prior_by_identity[str(identity)] = field

    for field in new_fields:
        identities = [field.get("native_full_name"), field.get("native_object_id"), field.get("id")]
        prior = next(
            (prior_by_identity[str(identity)] for identity in identities if identity and str(identity) in prior_by_identity),
            None,
        )
        if prior is None:
            continue
        if prior.get("label_origin") == "human":
            field["label"] = prior.get("label")
            field["label_origin"] = "human"
        if prior.get("semantic_type_origin") == "human":
            field["semantic_type"] = prior.get("semantic_type")
            field["semantic_type_origin"] = "human"
        if prior.get("notes"):
            field["notes"] = prior["notes"]
        # Keep a user-configured output date format only when the new field is
        # still date-like. Native inspection constraints otherwise win.
        old_constraints = prior.get("constraints") or {}
        new_constraints = field.get("constraints") or {}
        if old_constraints.get("date_format") and new_constraints.get("format_hint") == "date":
            field["constraints"] = {**new_constraints, "date_format": old_constraints["date_format"]}
    result["fields"] = new_fields
    return result


def ingest_evidence(session: Session, storage: ObjectStorage, run_id: str) -> None:
    """Parse first, then perform semantic extraction into one immutable snapshot."""
    run = session.get(ProcessingRun, run_id)
    if not run:
        raise KeyError(run_id)
    run.status = RunStatus.RUNNING
    run.stage = "parsing"
    run.progress = 8
    run.started_at = datetime.now(UTC)
    session.commit()
    try:
        with storage.open(run.artifact.storage_key) as source:
            content = source.read()
        settings = get_settings()
        allow_native_fallback = bool(run.config_snapshot.get("allow_native_fallback", False))
        # This flag is set only by deterministic test/evaluation callers. It
        # deliberately wins over ambient developer credentials so CI fixtures
        # never become dependent on a live provider response.
        if allow_native_fallback:
            parsed = native_parse(run.artifact.kind.value, content)
            provider = "native-test-fallback"
        elif settings.reducto_api_key:
            adapter = ReductoParserAdapter(settings.reducto_api_key, settings.reducto_base_url)
            job_id = adapter.submit(run.artifact.filename, content)
            run.provider_job_id = job_id
            run.provider = "reducto"
            session.commit()
            # Reducto queues larger/scanned documents asynchronously. Allow up to
            # two minutes before marking the ingestion run as timed out.
            for _ in range(240):
                candidate = adapter.poll(job_id)
                if candidate is not None:
                    parsed = candidate
                    break
                time.sleep(0.5)
            else:
                raise TimeoutError("Reducto parse did not finish within the ingestion window")
            provider = "reducto"
        else:
            raise RuntimeError(
                "Reducto is required for evidence ingestion. Configure REDUCTO_API_KEY; "
                "native OCR is disabled for production evidence."
            )
        run.stage = "semantic-extraction"
        run.progress = 65
        run.provider = provider
        run.parser_version = parsed.parser_version
        run.raw_result = parsed.raw
        session.commit()
        if allow_native_fallback:
            payload = extract_evidence(
                parsed.blocks,
                run.artifact_id,
                run.artifact.sha256,
                provider,
                parsed.parser_version,
            )
        else:
            payload = extract_evidence_model(
                parsed.blocks,
                run.artifact_id,
                run.artifact.sha256,
                provider,
                parsed.parser_version,
                session=session,
                run_id=run.id,
            )
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        snapshot_hash = hashlib.sha256(canonical).hexdigest()
        snapshot = session.scalar(
            select(EvidenceSnapshot).where(
                EvidenceSnapshot.artifact_id == run.artifact_id,
                EvidenceSnapshot.snapshot_sha256 == snapshot_hash,
            )
        )
        if snapshot is None:
            snapshot = EvidenceSnapshot(
                artifact_id=run.artifact_id,
                run_id=run.id,
                parser_provider=provider,
                parser_version=parsed.parser_version,
                extractor_version=payload["extractor_version"],
                snapshot_sha256=snapshot_hash,
                snapshot=payload,
            )
            session.add(snapshot)
            session.flush()
        run.result = {"snapshot_id": snapshot.id, **payload}
        run.status = RunStatus.SUCCEEDED
        run.stage = "complete"
        run.progress = 100
        run.finished_at = datetime.now(UTC)
        session.commit()
    except Exception as exc:
        session.rollback()
        run = session.get(ProcessingRun, run_id)
        if run:
            run.status = RunStatus.FAILED
            run.error = f"{type(exc).__name__}: {exc}"
            run.finished_at = datetime.now(UTC)
            session.commit()
        raise


def update_draft(session: Session, draft: TemplateDraft, expected_revision: int, schema: TemplateSchema, name: str | None) -> TemplateDraft:
    if draft.revision != expected_revision:
        raise RevisionConflict(f"Expected revision {expected_revision}, current revision is {draft.revision}")
    draft.schema = schema.model_dump(mode="json")
    if name is not None:
        draft.name = name
    draft.revision += 1
    session.commit()
    session.refresh(draft)
    return draft


def publish_draft(session: Session, draft: TemplateDraft, expected_revision: int) -> TemplateVersion:
    if draft.revision != expected_revision:
        raise RevisionConflict(f"Expected revision {expected_revision}, current revision is {draft.revision}")
    version_number = session.scalar(select(func.max(TemplateVersion.version)).where(TemplateVersion.draft_id == draft.id)) or 0
    canonical = json.dumps(draft.schema, sort_keys=True, separators=(",", ":")).encode()
    version = TemplateVersion(
        draft_id=draft.id,
        version=version_number + 1,
        name=draft.name,
        schema=draft.schema,
        source_revision=draft.revision,
        schema_sha256=hashlib.sha256(canonical).hexdigest(),
    )
    session.add(version)
    session.commit()
    session.refresh(version)
    return version


def seed_agencies(session: Session) -> None:
    """Ensure the configured agencies exist without overwriting edited details."""
    for seed in SEED_AGENCIES:
        if session.scalar(select(Agency).where(Agency.key == seed["key"])) is None:
            session.add(
                Agency(
                    key=seed["key"],
                    name=seed["name"],
                    details=dict(seed["details"]),
                    is_default=seed["is_default"],
                )
            )
    session.commit()


def resolve_agency(session: Session, agency_key: str | None) -> Agency:
    if agency_key:
        agency = session.scalar(select(Agency).where(Agency.key == agency_key))
        if agency is None:
            raise ValueError(f"Unknown agency {agency_key}")
        return agency
    agency = session.scalar(select(Agency).where(Agency.is_default.is_(True)))
    if agency is None:
        raise ValueError("No default agency is configured")
    return agency


def fill_plan_profile_identity(
    session: Session, agency_key: str | None, gateway: ModelGateway | None = None
) -> tuple[str, str]:
    """Return immutable plan profile and mapper profile hashes."""
    settings = gateway.settings if gateway is not None else get_settings()
    agency = resolve_agency(session, agency_key)
    facts = agency_facts({"key": agency.key, "name": agency.name, "details": agency.details})
    agency_rows = [
        {**fact, "snapshot_id": AGENCY_SNAPSHOT_ID, "source_kind": "agency"}
        for fact in facts
    ]
    agency_hash = hashlib.sha256(
        json.dumps(agency_rows, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    mapping_hash = mapping_profile_hash(
        model=settings.openai_mapping_model,
        reasoning_effort=settings.openai_reasoning_effort,
        prompt_version=MAPPING_PROMPT_VERSION,
        schema_version=MAPPING_SCHEMA_VERSION,
        mapper_version=MODEL_MAPPER_VERSION,
        agency_facts_hash=agency_hash,
    )
    return mapping_hash, mapping_hash


def new_case_key(session: Session) -> str:
    """Derive a case identifier for one upload batch, keeping sources isolated."""
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    existing = set(
        session.scalars(
            select(Artifact.case_key).where(Artifact.case_key.like(f"case-{stamp}-%"))
        ).all()
    )
    for sequence in range(1, 1000):
        candidate = f"case-{stamp}-{sequence:03d}"
        if candidate not in existing:
            return candidate
    raise ValueError(f"Exhausted case identifiers for {stamp}")


def create_fill_plan(
    session: Session,
    case_key: str,
    template_version: TemplateVersion,
    snapshot_ids: list[str] | None = None,
    agency_key: str | None = None,
) -> FillPlan:
    agency = resolve_agency(session, agency_key)
    if snapshot_ids is None:
        rows = session.execute(
            select(EvidenceSnapshot, Artifact)
            .join(Artifact, EvidenceSnapshot.artifact_id == Artifact.id)
            .where(Artifact.case_key == case_key)
            .order_by(EvidenceSnapshot.created_at.desc(), EvidenceSnapshot.id.desc())
        ).all()
        # A case bundle contains one current immutable snapshot per source artifact.
        # Older parser/extractor snapshots remain addressable but are never mixed
        # into a new Fill Plan implicitly.
        latest_by_artifact: dict[str, EvidenceSnapshot] = {}
        for snapshot, artifact in rows:
            latest_by_artifact.setdefault(artifact.id, snapshot)
        snapshots = list(latest_by_artifact.values())
    else:
        snapshots = list(
            session.scalars(select(EvidenceSnapshot).where(EvidenceSnapshot.id.in_(snapshot_ids))).all()
        )
        if len(snapshots) != len(set(snapshot_ids)):
            raise ValueError("One or more evidence snapshots were not found")
        artifacts = list(
            session.scalars(
                select(Artifact).where(Artifact.id.in_([snapshot.artifact_id for snapshot in snapshots]))
            ).all()
        )
        if any(artifact.case_key != case_key for artifact in artifacts):
            raise ValueError("Evidence snapshots must all belong to the requested case")
    if not snapshots:
        raise ValueError(f"Case {case_key} has no evidence snapshots")
    ordered = sorted(snapshots, key=lambda item: item.id)
    bundle_material = [
        {"id": snapshot.id, "sha256": snapshot.snapshot_sha256} for snapshot in ordered
    ]
    bundle_hash = hashlib.sha256(
        json.dumps(bundle_material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    bundle = session.scalar(
        select(EvidenceBundle).where(
            EvidenceBundle.case_key == case_key, EvidenceBundle.bundle_sha256 == bundle_hash
        )
    )
    if bundle is None:
        bundle = EvidenceBundle(
            case_key=case_key,
            snapshot_ids=[snapshot.id for snapshot in ordered],
            bundle_sha256=bundle_hash,
        )
        session.add(bundle)
        session.flush()
    existing = session.scalar(
        select(FillPlan).where(
            FillPlan.template_version_id == template_version.id,
            FillPlan.evidence_bundle_id == bundle.id,
            FillPlan.agency_key == agency.key,
        )
    )
    if existing:
        return existing
    payload = build_fill_plan(
        template_version.schema,
        [(snapshot.id, snapshot.snapshot) for snapshot in ordered],
        candidate_pools(session, ordered, template_version.schema.get("fields", [])),
        agency_facts({"key": agency.key, "name": agency.name, "details": agency.details}),
    )
    payload["evidence_bundle"] = {
        "id": bundle.id,
        "case_key": case_key,
        "snapshot_ids": bundle.snapshot_ids,
        "sha256": bundle.bundle_sha256,
    }
    payload["template_version"] = {
        "id": template_version.id,
        "version": template_version.version,
        "schema_sha256": template_version.schema_sha256,
    }
    payload["agency"] = {
        "key": agency.key,
        "name": agency.name,
        "source_version": AGENCY_SOURCE_VERSION,
    }
    fill_plan = FillPlan(
        case_key=case_key,
        agency_key=agency.key,
        template_version_id=template_version.id,
        evidence_bundle_id=bundle.id,
    )
    session.add(fill_plan)
    session.flush()
    revision = _fill_plan_revision(fill_plan.id, 1, payload)
    session.add(revision)
    session.commit()
    session.refresh(fill_plan)
    return fill_plan


def create_model_fill_plan(
    session: Session,
    *,
    run_id: str,
    case_key: str,
    template_version: TemplateVersion,
    snapshot_ids: list[str] | None = None,
    agency_key: str | None = None,
    gateway: ModelGateway | None = None,
) -> FillPlan:
    """Create a mapped plan in one transaction using frozen evidence snapshots.

    Model execution traces are stored independently by ModelGateway; the
    evidence bundle and Fill Plan revision commit after mapping succeeds.
    """
    gateway = gateway or ModelGateway()
    agency = resolve_agency(session, agency_key)
    snapshots = _resolve_fill_plan_snapshots(session, case_key, snapshot_ids)
    ordered = sorted(snapshots, key=lambda item: item.id)
    bundle_material = [{"id": item.id, "sha256": item.snapshot_sha256} for item in ordered]
    bundle_hash = hashlib.sha256(
        json.dumps(bundle_material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    bundle = session.scalar(
        select(EvidenceBundle).where(
            EvidenceBundle.case_key == case_key,
            EvidenceBundle.bundle_sha256 == bundle_hash,
        )
    )
    if bundle is None:
        bundle = EvidenceBundle(
            id=new_id(),
            case_key=case_key,
            snapshot_ids=[item.id for item in ordered],
            bundle_sha256=bundle_hash,
        )

    agency_payload = agency_facts(
        {"key": agency.key, "name": agency.name, "details": agency.details}
    )
    profile_hash, mapping_hash = fill_plan_profile_identity(session, agency.key, gateway)
    model = gateway.model_for("mapping")
    existing = session.scalar(
        select(FillPlan).where(
            FillPlan.template_version_id == template_version.id,
            FillPlan.evidence_bundle_id == bundle.id,
            FillPlan.agency_key == agency.key,
            FillPlan.mapping_profile_hash == profile_hash,
        )
    )
    if existing:
        return existing

    payload = build_model_fill_plan(
        template_version.schema,
        [(item.id, {**item.snapshot, "_artifact_id": item.artifact_id}) for item in ordered],
        agency_payload,
        session=session,
        run_id=run_id,
        gateway=gateway,
    )
    if payload.get("mapping_profile_hash") != mapping_hash:
        raise RuntimeError("Mapping profile hash differs from the persisted Fill Plan profile identity")
    payload["evidence_bundle"] = {
        "id": bundle.id, "case_key": case_key,
        "snapshot_ids": bundle.snapshot_ids, "sha256": bundle.bundle_sha256,
    }
    payload["template_version"] = {
        "id": template_version.id, "version": template_version.version,
        "schema_sha256": template_version.schema_sha256,
    }
    payload["agency"] = {"key": agency.key, "name": agency.name, "source_version": AGENCY_SOURCE_VERSION}
    payload["processing_run_id"] = run_id
    payload["model_profile"] = {
        **payload.get("model_profile", {}), "mapping_profile_hash": mapping_hash,
        "profile_hash": profile_hash, "mapping_model": model,
        "mapping_prompt_version": MAPPING_PROMPT_VERSION,
        "mapping_schema_version": MAPPING_SCHEMA_VERSION,
        "mapper_version": MODEL_MAPPER_VERSION,
    }
    validation = deterministic_validate(payload)
    payload["validation"] = validation
    known = {(item.get("target_id"), item["code"]) for item in payload.get("issues", [])}
    already_required = {target_id for target_id, code in known if code == "required_field_blank"}
    for finding in validation["findings"]:
        if finding["code"] == "required_value_missing" and finding.get("target_id") in already_required:
            continue
        key = (finding.get("target_id"), finding["code"])
        if key not in known:
            payload.setdefault("issues", []).append({
                "id": finding["id"], "code": finding["code"],
                "severity": finding["severity"], "target_id": finding.get("target_id"),
                "message": finding["message"],
            })
            known.add(key)
    payload["summary"] = {
        "target_count": len(payload.get("targets", [])),
        "proposed_count": sum(target.get("selected_candidate_id") is not None for target in payload.get("targets", [])),
        "unresolved_count": sum(target.get("state") == "unresolved" for target in payload.get("targets", [])),
        "issue_count": len(payload.get("issues", [])),
        "blocker_count": sum(issue.get("severity") == "blocker" for issue in payload.get("issues", [])),
    }

    fill_plan = FillPlan(
        case_key=case_key, agency_key=agency.key,
        template_version_id=template_version.id,
        evidence_bundle_id=bundle.id, mapping_profile_hash=profile_hash,
    )
    if session.get(EvidenceBundle, bundle.id) is None:
        session.add(bundle)
    session.add(fill_plan)
    session.flush()
    session.add(_fill_plan_revision(fill_plan.id, 1, payload))
    return fill_plan


def _resolve_fill_plan_snapshots(
    session: Session, case_key: str, snapshot_ids: list[str] | None
) -> list[EvidenceSnapshot]:
    if snapshot_ids is None:
        rows = session.execute(
            select(EvidenceSnapshot, Artifact)
            .join(Artifact, EvidenceSnapshot.artifact_id == Artifact.id)
            .where(Artifact.case_key == case_key)
            .order_by(EvidenceSnapshot.created_at.desc(), EvidenceSnapshot.id.desc())
        ).all()
        latest_by_artifact: dict[str, EvidenceSnapshot] = {}
        for snapshot, artifact in rows:
            latest_by_artifact.setdefault(artifact.id, snapshot)
        snapshots = list(latest_by_artifact.values())
    else:
        snapshots = list(
            session.scalars(
                select(EvidenceSnapshot).where(EvidenceSnapshot.id.in_(snapshot_ids))
            ).all()
        )
        if len(snapshots) != len(set(snapshot_ids)):
            raise ValueError("One or more evidence snapshots were not found")
        artifacts = list(
            session.scalars(
                select(Artifact).where(
                    Artifact.id.in_([item.artifact_id for item in snapshots])
                )
            ).all()
        )
        if len(artifacts) != len(snapshots) or any(
            artifact.case_key != case_key for artifact in artifacts
        ):
            raise ValueError("Evidence snapshots must all belong to the requested case")
    if not snapshots:
        raise ValueError(f"Case {case_key} has no evidence snapshots")
    return snapshots


def run_fill_plan_job(session: Session, run_id: str, gateway: ModelGateway | None = None) -> None:
    """Execute one queued Fill Plan run and atomically publish its artifacts."""
    run = session.get(ProcessingRun, run_id)
    if run is None:
        raise KeyError(run_id)
    config = dict(run.config_snapshot or {})
    if config.get("operation") != "fill_plan":
        raise ValueError("Processing run is not a Fill Plan operation")
    run.status = RunStatus.RUNNING
    run.stage = "mapping"
    run.progress = 15
    run.started_at = datetime.now(UTC)
    run.error = None
    session.commit()
    try:
        version = session.get(TemplateVersion, config["template_version_id"])
        if version is None:
            raise ValueError("Published template version no longer exists")
        run.stage = "mapping"
        run.progress = 20
        session.commit()
        plan = create_model_fill_plan(
            session,
            run_id=run.id,
            case_key=config["case_key"],
            template_version=version,
            snapshot_ids=config.get("evidence_snapshot_ids"),
            agency_key=config.get("agency_key"),
            gateway=gateway,
        )
        run.stage = "complete"
        run.progress = 95
        run.result = {"fill_plan_id": plan.id}
        run.status = RunStatus.SUCCEEDED
        run.stage = "complete"
        run.progress = 100
        run.finished_at = datetime.now(UTC)
        session.commit()
    except Exception as exc:
        session.rollback()
        failed = session.get(ProcessingRun, run_id)
        if failed is not None:
            failed.status = RunStatus.FAILED
            failed.stage = "failed"
            failed.error = f"{type(exc).__name__}: {exc}"
            failed.result = None
            failed.finished_at = datetime.now(UTC)
            session.commit()
        # Keep the failure visible in GET /processing-runs, rather than
        # bubbling into BackgroundTasks and obscuring the committed run state.


def revise_fill_plan(
    session: Session,
    fill_plan: FillPlan,
    expected_revision: int,
    selected_candidates: dict[str, str | None],
    derivations: list[dict[str, object]],
) -> FillPlanRevision:
    if fill_plan.current_revision != expected_revision:
        raise RevisionConflict(
            f"Expected revision {expected_revision}, current revision is {fill_plan.current_revision}"
        )
    current = session.scalar(
        select(FillPlanRevision).where(
            FillPlanRevision.fill_plan_id == fill_plan.id,
            FillPlanRevision.revision == fill_plan.current_revision,
        )
    )
    if current is None:
        raise ValueError("Current Fill Plan revision is missing")
    human_targets = {
        target.get("field", {}).get("id")
        for target in current.payload.get("targets", [])
        if target.get("review", {}).get("origin") == "human"
    }
    attempted = human_targets.intersection(selected_candidates)
    if attempted:
        raise ValueError(
            "Human-reviewed targets have terminal authority and cannot be changed by mapping: "
            + ", ".join(sorted(attempted))
        )
    payload = apply_revision(current.payload, selected_candidates, derivations)
    next_number = fill_plan.current_revision + 1
    revision = _fill_plan_revision(fill_plan.id, next_number, payload)
    session.add(revision)
    fill_plan.current_revision = next_number
    session.commit()
    session.refresh(revision)
    return revision


def review_fill_plan(
    session: Session,
    fill_plan: FillPlan,
    *,
    expected_revision: int,
    target_field_id: str,
    action: str,
    actor: str,
    reason: str | None,
    candidate_id: str | None,
    value: object,
) -> ReviewDecision:
    if fill_plan.current_revision != expected_revision:
        raise RevisionConflict(
            f"Expected revision {expected_revision}, current revision is {fill_plan.current_revision}"
        )
    current = session.scalar(
        select(FillPlanRevision).where(
            FillPlanRevision.fill_plan_id == fill_plan.id,
            FillPlanRevision.revision == expected_revision,
        )
    )
    if current is None:
        raise ValueError("Current Fill Plan revision is missing")
    decision = ReviewDecision(
        id=new_id(),
        fill_plan_id=fill_plan.id,
        source_revision_id=current.id,
        resulting_revision_id="",
        target_field_id=target_field_id,
        action=action,
        actor=actor,
        reason=reason,
    )
    payload, audit = apply_review_decision(
        current.payload,
        decision_id=decision.id,
        target_field_id=target_field_id,
        action=action,
        actor=actor,
        reason=reason,
        candidate_id=candidate_id,
        value=value,
    )
    next_number = expected_revision + 1
    revision = _fill_plan_revision(fill_plan.id, next_number, payload)
    revision.mapper_version = f"{current.mapper_version}+human-review-v1"
    session.add(revision)
    session.flush()
    decision.resulting_revision_id = revision.id
    decision.candidate_id = audit["candidate_id"]
    decision.previous_value = audit["previous_value"]
    decision.new_value = audit["new_value"]
    decision.detail = audit["detail"]
    session.add(decision)
    fill_plan.current_revision = next_number
    session.commit()
    session.refresh(decision)
    return decision


def _fill_plan_revision(fill_plan_id: str, revision: int, payload: dict) -> FillPlanRevision:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return FillPlanRevision(
        fill_plan_id=fill_plan_id,
        revision=revision,
        mapper_version=payload.get("mapper_version", MAPPER_VERSION),
        payload_sha256=hashlib.sha256(canonical).hexdigest(),
        payload=payload,
    )
