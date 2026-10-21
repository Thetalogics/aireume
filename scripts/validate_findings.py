"""Validate the production-readiness Markdown finding ledger (Python 3.11+)."""

import re
from pathlib import Path


BASELINE_IDS = {
    "SEC-001", "SUP-001", "FUN-001", "REL-001", "DOC-001", "PERF-001",
    "UI-001", "OPS-001", "OPS-002", "ARC-001", "QA-001", "QA-002",
    "QA-003", "DOC-002",
}
STATES = {
    "discovered", "reproduced", "test added", "fixed",
    "focused verification", "full verification", "closed", "inapplicable",
}
CLOSURE_FIELDS = {
    "root cause", "files changed", "tests", "focused result",
    "full-suite result", "evidence", "residual risk",
}
REQUIRED_COLUMNS = CLOSURE_FIELDS | {
    "id", "priority", "area", "required outcome", "owner", "state",
    "reproduction steps", "screenshots",
}


def validate_findings(path: Path) -> list[str]:
    r"""Return structural errors; open rows may leave closure fields empty.

    The ledger uses one pipe-delimited table with the documented columns.
    Literal pipes inside cells must be escaped as ``\|``. Validation checks
    recorded evidence fields, not whether their claims or linked files are true.
    """
    errors = []
    seen = set()
    headers = None
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        if headers is None:
            if cells[0].lower() != "id":
                continue
            headers = [cell.lower() for cell in cells]
            missing = REQUIRED_COLUMNS - set(headers)
            if missing:
                return ["Missing required columns: " + ", ".join(sorted(missing))]
            if len(headers) != len(set(headers)):
                return ["Duplicate column names"]
            continue
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        if len(cells) != len(headers):
            errors.append(f"Line {number}: expected {len(headers)} columns, got {len(cells)}")
            continue
        row = dict(zip(headers, cells))
        finding_id = row["id"]
        if finding_id not in BASELINE_IDS:
            errors.append(f"Line {number}: unknown ID {finding_id!r}")
        if finding_id in seen:
            errors.append(f"Line {number}: duplicate ID {finding_id}")
        seen.add(finding_id)
        state = row["state"]
        if state not in STATES:
            errors.append(f"{finding_id}: invalid state {state!r}")
        evidence_fields = set()
        if state == "closed":
            evidence_fields = CLOSURE_FIELDS
            if finding_id.startswith("UI-"):
                evidence_fields = evidence_fields | {"screenshots"}
        elif state == "inapplicable":
            evidence_fields = {"reproduction steps", "evidence"}
        for field in sorted(evidence_fields):
            if not row[field]:
                errors.append(f"{finding_id}: {state} finding missing {field}")
    if headers is None:
        errors.append("Missing findings table with required columns")
    for finding_id in sorted(BASELINE_IDS - seen):
        errors.append(f"Missing baseline ID {finding_id}")
    return errors
