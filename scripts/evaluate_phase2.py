"""Evaluate Phase 2 against authorized source members without extracting archives to disk."""

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

from app.evidence import extract_evidence, native_parse

CASES = {
    "case-1": ["California's.pdf", "Jeev Mail - Limousine_ Elite Chauffer LLC.pdf"],
    "case-2": ["Mohammad-Khalifeh-08-19-2026 (2).pdf"],
}


def evaluate(zip_paths: dict[str, Path]) -> dict:
    documents = []
    failures = []
    for case_key, members in CASES.items():
        with zipfile.ZipFile(zip_paths[case_key]) as archive:
            for member in members:
                content = archive.read(member)
                source_hash = hashlib.sha256(content).hexdigest()
                parsed = native_parse("pdf", content)
                snapshot = extract_evidence(
                    parsed.blocks,
                    f"evaluation:{case_key}:{member}",
                    source_hash,
                    "native",
                    parsed.parser_version,
                )
                accepted = [fact for fact in snapshot["facts"] if fact["accepted"]]
                if any(not fact["provenance"] for fact in accepted):
                    failures.append(f"{member}: an accepted fact has no provenance")
                if any(
                    fact["accepted"]
                    and ("proposed_case_direction" in fact["semantics"] or "signature_marker" in fact["semantics"])
                    for fact in snapshot["facts"]
                ):
                    failures.append(f"{member}: untrusted direction or signature was accepted")
                explicit_absence = any("explicit_absence" in fact["semantics"] for fact in snapshot["facts"])
                if member == "California's.pdf" and not explicit_absence:
                    failures.append(f"{member}: explicit no-given-name semantics were not preserved")
                review = [fact for fact in snapshot["facts"] if not fact["accepted"]]
                observations = []
                if snapshot["unreadable_regions"]:
                    observations.append(f"{len(snapshot['unreadable_regions'])} unreadable/low-confidence regions")
                if review:
                    observations.append(f"{len(review)} facts require review")
                if member == "California's.pdf" and not any(
                    fact["key"] == "driver.license_number" for fact in snapshot["facts"]
                ):
                    observations.append("license number not recovered by local OCR; no value guessed")
                documents.append(
                    {
                        "case": case_key,
                        "source": member,
                        "sha256": source_hash,
                        "parse_blocks": len(parsed.blocks),
                        "facts": len(snapshot["facts"]),
                        "accepted_facts": len(accepted),
                        "review_facts": len(review),
                        "unreadable_regions": len(snapshot["unreadable_regions"]),
                        "explicit_absence": explicit_absence,
                        "observations": observations,
                    }
                )
    return {
        "status": "passed" if not failures else "failed",
        "policy": "Only original source members were read; targets and FILLED outputs were excluded.",
        "documents": documents,
        "failures": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("zip1", type=Path)
    parser.add_argument("zip2", type=Path)
    args = parser.parse_args()
    result = evaluate({"case-1": args.zip1, "case-2": args.zip2})
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
