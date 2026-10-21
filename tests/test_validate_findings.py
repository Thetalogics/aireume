from pathlib import Path

import pytest

from scripts.validate_findings import validate_findings


BASELINE_IDS = (
    "SEC-001", "SUP-001", "FUN-001", "REL-001", "DOC-001", "PERF-001",
    "UI-001", "OPS-001", "OPS-002", "ARC-001", "QA-001", "QA-002",
    "QA-003", "DOC-002",
)
HEADERS = (
    "ID", "Priority", "Area", "Required outcome", "Owner", "State",
    "Root cause", "Files changed", "Tests", "Focused result",
    "Full-suite result", "Reproduction steps", "Evidence", "Residual risk",
    "Screenshots",
)


def write_ledger(tmp_path, *, updates=None, extra_rows=(), omit=(), headers=HEADERS):
    rows = []
    for finding_id in BASELINE_IDS:
        if finding_id in omit:
            continue
        row = dict.fromkeys(HEADERS, "")
        row.update(ID=finding_id, Priority="P2", Area="Audit", Owner="Unassigned",
                   State="reproduced", **{"Required outcome": "Resolve finding"})
        row.update((updates or {}).get(finding_id, {}))
        rows.append(row)
    rows.extend(extra_rows)
    lines = ["| " + " | ".join(headers) + " |",
             "| " + " | ".join("---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(row.get(key, "") for key in headers) + " |"
                 for row in rows)
    path = tmp_path / "FINDINGS.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def closed_row():
    return {
        "State": "closed", "Root cause": "Unsafe path construction",
        "Files changed": "app/backend/routes/upload.py",
        "Tests": "test_upload_traversal", "Focused result": "1 passed",
        "Full-suite result": "100 passed", "Reproduction steps": "Upload ../x",
        "Evidence": "Verified containment", "Residual risk": "none known",
    }


def test_valid_open_ledger(tmp_path):
    assert validate_findings(write_ledger(tmp_path)) == []


def test_duplicate_id_is_rejected(tmp_path):
    path = write_ledger(tmp_path, extra_rows=[{"ID": "SEC-001", "State": "reproduced"}])
    assert any("duplicate" in error.lower() and "SEC-001" in error
               for error in validate_findings(path))


def test_unknown_id_is_rejected(tmp_path):
    path = write_ledger(tmp_path, extra_rows=[{"ID": "SEC-999", "State": "reproduced"}])
    assert any("unknown" in error.lower() and "SEC-999" in error
               for error in validate_findings(path))


@pytest.mark.parametrize("state", ["deferred", "suppressed", "unknown", ""])
def test_unknown_state_is_rejected(tmp_path, state):
    path = write_ledger(tmp_path, updates={"SEC-001": {"State": state}})
    assert any("state" in error.lower() and "SEC-001" in error
               for error in validate_findings(path))


@pytest.mark.parametrize("state", [
    "discovered", "reproduced", "test added", "fixed",
    "focused verification", "full verification",
])
def test_documented_open_states_are_valid(tmp_path, state):
    path = write_ledger(tmp_path, updates={"SEC-001": {"State": state}})
    assert validate_findings(path) == []


@pytest.mark.parametrize("field", [
    "Root cause", "Files changed", "Tests", "Focused result",
    "Full-suite result", "Evidence", "Residual risk",
])
def test_closed_finding_requires_closure_fields(tmp_path, field):
    row = closed_row()
    row[field] = " "
    path = write_ledger(tmp_path, updates={"SEC-001": row})
    assert any("SEC-001" in error and field.lower() in error.lower()
               for error in validate_findings(path))


def test_closed_finding_with_evidence_is_valid(tmp_path):
    assert validate_findings(write_ledger(tmp_path, updates={"SEC-001": closed_row()})) == []


def test_visual_closure_requires_before_after_screenshots(tmp_path):
    row = closed_row()
    path = write_ledger(tmp_path, updates={"UI-001": row})
    assert any("UI-001" in error and "screenshots" in error.lower()
               for error in validate_findings(path))
    row["Screenshots"] = "before.png / after.png"
    assert validate_findings(write_ledger(tmp_path, updates={"UI-001": row})) == []


@pytest.mark.parametrize("field", ["Reproduction steps", "Evidence"])
def test_inapplicable_requires_reproduction_and_evidence(tmp_path, field):
    row = {"State": "inapplicable", "Reproduction steps": "Attempted reproduction",
           "Evidence": "Feature absent in deployed product"}
    row[field] = ""
    path = write_ledger(tmp_path, updates={"SEC-001": row})
    assert any("SEC-001" in error and field.lower() in error.lower()
               for error in validate_findings(path))


def test_evidenced_inapplicable_finding_is_valid(tmp_path):
    row = {"State": "inapplicable", "Reproduction steps": "Attempted reproduction",
           "Evidence": "Feature absent in deployed product"}
    assert validate_findings(write_ledger(tmp_path, updates={"SEC-001": row})) == []


def test_missing_baseline_id_is_rejected(tmp_path):
    assert any("missing" in error.lower() and "DOC-002" in error
               for error in validate_findings(write_ledger(tmp_path, omit={"DOC-002"})))


def test_missing_required_column_is_rejected(tmp_path):
    path = write_ledger(tmp_path, headers=tuple(h for h in HEADERS if h != "Evidence"))
    assert any("column" in error.lower() and "evidence" in error.lower()
               for error in validate_findings(path))


def test_malformed_row_is_rejected(tmp_path):
    path = write_ledger(tmp_path)
    with path.open("a", encoding="utf-8") as ledger:
        ledger.write("| SEC-001 | reproduced |\n")
    assert any("column" in error.lower() for error in validate_findings(path))


def test_empty_file_is_rejected(tmp_path):
    path = tmp_path / "empty.md"
    path.write_text("", encoding="utf-8")
    assert validate_findings(path)


def test_escaped_pipe_in_evidence_is_valid(tmp_path):
    row = closed_row()
    row["Evidence"] = r"Verified A \| B"
    assert validate_findings(write_ledger(tmp_path, updates={"SEC-001": row})) == []


def assert_complete_ledger(path):
    assert validate_findings(path) == []
    rows = [line.split("|") for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith("| ") and line.split("|")[1].strip() in BASELINE_IDS]
    assert {cells[1].strip() for cells in rows} == set(BASELINE_IDS)
    assert len(rows) == 14


def test_repository_ledger_contains_every_baseline_finding():
    source = Path(__file__).resolve().parents[1] / "docs/production-readiness/FINDINGS.md"
    assert_complete_ledger(source)


def test_complete_ledger_can_close_a_finding(tmp_path):
    assert_complete_ledger(write_ledger(tmp_path, updates={"SEC-001": closed_row()}))
