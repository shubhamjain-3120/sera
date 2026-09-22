"""Audit persisted Phase 3 Fill Plans without reading reference outputs."""

import argparse
import json

from sqlalchemy import select

from app.db import SessionLocal
from app.models import FillPlan, FillPlanRevision


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--require-plans", action="store_true", help="fail when the database has no Fill Plans"
    )
    args = parser.parse_args()
    reports = []
    failures: list[str] = []
    with SessionLocal() as session:
        plans = session.scalars(select(FillPlan).order_by(FillPlan.created_at)).all()
        if args.require_plans and not plans:
            failures.append("no Fill Plans found")
        for plan in plans:
            revision = session.scalar(
                select(FillPlanRevision).where(
                    FillPlanRevision.fill_plan_id == plan.id,
                    FillPlanRevision.revision == plan.current_revision,
                )
            )
            if revision is None:
                failures.append(f"{plan.id}: current revision is missing")
                continue
            payload = revision.payload
            selected = 0
            grounded = 0
            for target in payload.get("targets", []):
                candidate_id = target.get("selected_candidate_id")
                if not candidate_id:
                    continue
                selected += 1
                candidates = {
                    candidate["id"]: candidate for candidate in target.get("candidates", [])
                }
                candidate = candidates.get(candidate_id)
                if candidate is None:
                    failures.append(f"{plan.id}:{target['field']['id']}: selected candidate is missing")
                    continue
                if candidate.get("origin") == "evidence" and candidate.get("provenance"):
                    grounded += 1
                elif candidate.get("origin") == "derivation":
                    depth = candidate.get("derivation", {}).get("depth", 99)
                    if depth <= 2 and candidate.get("review_required"):
                        grounded += 1
                    else:
                        failures.append(
                            f"{plan.id}:{target['field']['id']}: invalid selected derivation"
                        )
                else:
                    failures.append(
                        f"{plan.id}:{target['field']['id']}: selected value lacks evidence"
                    )
            summary = payload.get("summary", {})
            reports.append(
                {
                    "fill_plan_id": plan.id,
                    "case_key": plan.case_key,
                    "revision": plan.current_revision,
                    "selected": selected,
                    "selected_with_complete_grounding": grounded,
                    "unresolved": summary.get("unresolved_count", 0),
                    "issues": summary.get("issue_count", 0),
                    "blockers": summary.get("blocker_count", 0),
                    "evidence_bundle_sha256": payload.get("evidence_bundle", {}).get("sha256"),
                }
            )
    print(json.dumps({"plans": reports, "failures": failures}, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
