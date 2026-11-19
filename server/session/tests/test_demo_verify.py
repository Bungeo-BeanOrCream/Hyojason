"""B-08 smoke tests using the same input files as the rehearsal CLI."""

import json
from pathlib import Path

from hyojason_session.demo_verify import main


FIXTURES = Path(__file__).resolve().parents[3] / "demo" / "fixtures"


def test_n24_fixture_manifest_ready_without_live_attribution(capsys):
    assert main(["prepare", str(FIXTURES / "manifest.fixture.json"), "--source", "LIVE"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert (result["outcome"], result["diagnostics"]["source"]) == ("fixtures_ready", "FIXTURE")
    assert len(result["data"]["ready_scenarios"]) == 8
    assert result["data"]["blocked_scenarios"] == []


def test_n25_fixed_plan_preserves_first_attempt_and_missing_evidence(capsys):
    assert main(["evaluate", str(FIXTURES), str(FIXTURES / "evidence.fixture.json")]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["outcome"] == "evidence_incomplete"
    rows = {row["case_id"]: row for row in result["data"]["results"]}
    assert rows["I01"]["verdict"] == "PASS"
    assert (rows["I02"]["attempt"], rows["I02"]["verdict"]) == (1, "FAIL")
    assert rows["A01"]["verdict"] == "UNVERIFIED"
    assert result["data"]["live_pass_count"] == 0


def test_n25_fixture_inputs_cannot_pass_the_live_plan(capsys):
    assert main(["evaluate", str(FIXTURES), str(FIXTURES / "evidence.fixture.json"), "--source", "LIVE"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["data"]["live_pass_count"] == 0
    first = next(row for row in result["data"]["results"] if row["case_id"] == "I01")
    assert (first["verdict"], first["behavior_verdict"], first["source"]) == ("UNVERIFIED", "PASS", "FIXTURE")
