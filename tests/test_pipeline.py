"""End to end pipeline: upload, mapping pause, resume, quality report and rollback."""

from __future__ import annotations

import uuid
from pathlib import Path

import pandas as pd
import pyreadstat
from sqlalchemy import text

from app.analysis import mapping as mapping_module
from app.analysis.dictionary import DictionaryEntry, load_dictionary
from app.models.enums import AdminRole
from app.models.survey import Survey
from tests.conftest import API, admin_headers, confirm_mapping, upload_survey


def test_upload_requires_survey_permission(client, admin_factory, synthetic_dta):
    editor = admin_factory(role=AdminRole.CONTENT_EDITOR)
    response = upload_survey(client, admin_headers(editor), synthetic_dta)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "auth.forbidden"


def test_upload_rejects_non_dta_files(client, admin_factory):
    admin = admin_factory()
    response = client.post(
        f"{API}/admin/surveys/upload",
        headers=admin_headers(admin),
        files={"file": ("notes.txt", b"hello", "text/plain")},
        data={"name": "Bad file", "year": "2022"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "upload.invalid_type"


def test_upload_rejects_files_over_the_limit(client, admin_factory, synthetic_dta):
    from app.config import get_settings

    settings = get_settings()
    original = settings.max_upload_bytes
    settings.max_upload_bytes = 10
    try:
        admin = admin_factory()
        response = upload_survey(client, admin_headers(admin), synthetic_dta)
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "upload.too_large"
    finally:
        settings.max_upload_bytes = original


def test_upload_pauses_at_mapping_then_resumes_to_completion(client, admin_factory, synthetic_dta):
    admin = admin_factory()
    headers = admin_headers(admin)

    response = upload_survey(client, headers, synthetic_dta)
    assert response.status_code == 202
    body = response.json()
    survey_id = body["survey"]["id"]
    job_id = body["job"]["id"]
    assert body["survey"]["status"] == "awaiting_mapping"
    assert body["job"]["status"] == "awaiting_mapping"
    assert body["job"]["stage"] == "mapped"

    detail = client.get(f"{API}/admin/jobs/{job_id}", headers=headers).json()
    event_types = [event["type"] for event in detail["events"]]
    assert event_types[0] == "upload.received"
    assert "job.mapping_required" in event_types
    for expected in (
        "job.started",
        "job.stage_started",
        "job.stage_completed",
    ):
        assert expected in event_types

    dictionary = client.get(f"{API}/admin/surveys/{survey_id}/dictionary", headers=headers)
    assert dictionary.status_code == 200
    variables = {item["variable_name"]: item for item in dictionary.json()["variables"]}
    assert variables["h25_21"]["label"] == "Grew mushroom"
    assert variables["h25_21"]["value_labels"][0]["label"] == "Yes"

    proposal = client.get(f"{API}/admin/jobs/{job_id}/mapping", headers=headers).json()
    assert proposal["confirmed"] is False
    proposed = {item["variable_name"]: item for item in proposal["variables"]}
    assert proposed["h25_21"]["yes_code"] == "1"
    assert proposed["h25_21"]["platform_group"] == "vegetables"
    assert proposed["ml01"]["platform_group"] == "geography"

    confirmed = confirm_mapping(
        client,
        headers,
        job_id,
        updates=[{"variable_name": "h25_21", "yes_code": "1"}],
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "completed"
    assert confirmed.json()["stage"] == "loaded"
    assert confirmed.json()["progress_percent"] == 100

    survey = client.get(f"{API}/admin/surveys/{survey_id}", headers=headers).json()
    assert survey["status"] == "ready"
    assert survey["mapping_confirmed_at"] is not None

    quality = client.get(f"{API}/admin/surveys/{survey_id}/quality-report", headers=headers)
    assert quality.status_code == 200
    report = quality.json()
    assert report["households_loaded"] > 0
    assert report["persons_loaded"] > 0
    assert report["duplicate_persons"] > 0
    assert report["exact_duplicate_rows"] > 0
    assert report["household_variable_conflicts"] > 0
    assert report["persons_loaded"] > report["households_loaded"]

    rules = {item["rule"]: item["count"] for item in report["rule_violations"]}
    assert rules["impossible_age_to_null"] > 0
    assert rules["declared_missing_codes_to_null"] > 0
    assert rules["geography_name_standardised"] > 0

    events = client.get(f"{API}/admin/jobs/{job_id}", headers=headers).json()["events"]
    assert events[-1]["type"] == "job.completed"
    stage_completed = [event["stage"] for event in events if event["type"] == "job.stage_completed"]
    assert stage_completed == ["received", "validated", "mapped", "cleaned", "loaded"]


def test_loaded_rows_match_the_quality_report(client, admin_factory, synthetic_dta, db):
    admin = admin_factory()
    headers = admin_headers(admin)
    body = upload_survey(client, headers, synthetic_dta).json()
    confirm_mapping(client, headers, body["job"]["id"])

    survey_id = body["survey"]["id"]
    report = client.get(f"{API}/admin/surveys/{survey_id}/quality-report", headers=headers).json()

    households = db.scalar(
        text("SELECT count(*) FROM households WHERE survey_id = :survey_id"),
        {"survey_id": survey_id},
    )
    persons = db.scalar(
        text("SELECT count(*) FROM persons WHERE survey_id = :survey_id"),
        {"survey_id": survey_id},
    )
    assert households == report["households_loaded"]
    assert persons == report["persons_loaded"]
    assert report["rows_after_dedup"] == report["persons_loaded"]
    assert report["rows_raw"] > report["rows_after_dedup"]

    mismatched = db.scalar(
        text(
            """
            SELECT count(*) FROM persons p
            JOIN households h ON h.id = p.household_id
            WHERE p.survey_id = :survey_id AND p.hhid <> h.hhid
            """
        ),
        {"survey_id": survey_id},
    )
    assert mismatched == 0


def test_households_keep_only_dictionary_variables(client, admin_factory, synthetic_dta, db):
    admin = admin_factory()
    headers = admin_headers(admin)
    body = upload_survey(client, headers, synthetic_dta).json()
    confirm_mapping(client, headers, body["job"]["id"])
    survey_id = body["survey"]["id"]

    dictionary_names = set(load_dictionary(db, uuid.UUID(survey_id)))
    attributes = db.scalar(
        text("SELECT attributes FROM households WHERE survey_id = :survey_id LIMIT 1"),
        {"survey_id": survey_id},
    )
    assert attributes
    assert set(attributes) <= dictionary_names
    assert "h25_21" in attributes


def test_household_size_is_filled_from_person_rows(client, admin_factory, synthetic_dta, db):
    admin = admin_factory()
    headers = admin_headers(admin)
    body = upload_survey(client, headers, synthetic_dta).json()
    confirm_mapping(client, headers, body["job"]["id"])
    survey_id = body["survey"]["id"]

    sizes = db.execute(
        text(
            """
            SELECT h.hhid, h.household_size, count(p.id) AS persons
            FROM households h
            LEFT JOIN persons p ON p.household_id = h.id
            WHERE h.survey_id = :survey_id
            GROUP BY h.hhid, h.household_size
            """
        ),
        {"survey_id": survey_id},
    ).all()
    assert sizes
    for _hhid, household_size, persons in sizes:
        assert household_size == persons


def test_missing_key_variables_fails_the_job_without_loading_rows(
    client, admin_factory, tmp_path, db
):
    frame = pd.DataFrame({"hhid": ["1", "2"], "p01": [1.0, 2.0], "h25_21": [1.0, 2.0]})
    path = tmp_path / "incomplete.dta"
    pyreadstat.write_dta(frame, str(path), column_labels=["Household", "Person", "Mushroom"])

    admin = admin_factory()
    headers = admin_headers(admin)
    body = upload_survey(client, headers, str(path)).json()

    assert body["job"]["status"] == "failed"
    assert body["job"]["error_code"] == "survey.missing_key_variables"
    assert body["job"]["error_translation_key"] == "errors.survey.missing_key_variables"
    assert "ml01" in body["job"]["error_message"]
    assert body["survey"]["status"] == "failed"

    households = db.scalar(
        text("SELECT count(*) FROM households WHERE survey_id = :survey_id"),
        {"survey_id": body["survey"]["id"]},
    )
    assert households == 0

    events = client.get(f"{API}/admin/jobs/{body['job']['id']}", headers=headers).json()["events"]
    assert events[-1]["type"] == "job.failed"
    assert events[-1]["payload"]["code"] == "survey.missing_key_variables"


def test_deleting_a_survey_rolls_back_its_rows(client, admin_factory, synthetic_dta, db):
    admin = admin_factory()
    headers = admin_headers(admin)
    body = upload_survey(client, headers, synthetic_dta).json()
    confirm_mapping(client, headers, body["job"]["id"])
    survey_id = body["survey"]["id"]

    stored_path = db.scalar(
        text("SELECT stored_path FROM surveys WHERE id = :survey_id"), {"survey_id": survey_id}
    )
    from app.config import get_settings

    file_path = Path(get_settings().upload_dir).resolve() / stored_path
    assert file_path.exists()

    deleted = client.delete(f"{API}/admin/surveys/{survey_id}", headers=headers)
    assert deleted.status_code == 200

    assert not file_path.exists()
    households = db.scalar(
        text("SELECT count(*) FROM households WHERE survey_id = :survey_id"),
        {"survey_id": survey_id},
    )
    persons = db.scalar(
        text("SELECT count(*) FROM persons WHERE survey_id = :survey_id"),
        {"survey_id": survey_id},
    )
    assert (households, persons) == (0, 0)

    missing = client.get(f"{API}/admin/surveys/{survey_id}", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "survey.not_found"


def test_surveys_list_supports_pagination(client, admin_factory, synthetic_dta):
    admin = admin_factory()
    headers = admin_headers(admin)
    for index in range(2):
        upload_survey(client, headers, synthetic_dta, name=f"Census {index}")

    response = client.get(
        f"{API}/admin/surveys", headers=headers, params={"page": 1, "page_size": 1}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["pagination"]["total"] == 2
    assert len(body["items"]) == 1


def test_mapping_reuse_prefills_from_a_confirmed_survey(db, admin_factory, synthetic_dta):
    from app.analysis.dictionary import build_dictionary, read_metadata

    entries = build_dictionary(read_metadata(synthetic_dta))
    admin = admin_factory()

    first = Survey(
        name="First",
        year=2022,
        original_filename="a.dta",
        stored_path="a/a.dta",
        file_size_bytes=1,
        checksum_sha256="0" * 64,
    )
    db.add(first)
    db.commit()

    proposal = mapping_module.propose_mappings(db, first.id, entries)
    assert any(row.yes_code for row in proposal)
    mapping_module.confirm_mappings(db, first, admin.id, [])

    # A file with the same variables but no value labels still gets the confirmed codes.
    label_free = [
        DictionaryEntry(
            variable_name=entry.variable_name,
            label=entry.label,
            section=entry.section,
            dtype=entry.dtype,
            position=entry.position,
        )
        for entry in entries
    ]
    second = Survey(
        name="Second",
        year=2022,
        original_filename="b.dta",
        stored_path="b/b.dta",
        file_size_bytes=1,
        checksum_sha256="1" * 64,
    )
    db.add(second)
    db.commit()

    second_proposal = mapping_module.propose_mappings(db, second.id, label_free)
    by_name = {row.variable_name: row for row in second_proposal}
    assert by_name["h25_21"].yes_code == "1"
    assert by_name["h25_21"].confirmed is False


def test_mapping_confirmation_rejects_unknown_variables(client, admin_factory, synthetic_dta):
    admin = admin_factory()
    headers = admin_headers(admin)
    body = upload_survey(client, headers, synthetic_dta).json()
    response = confirm_mapping(
        client, headers, body["job"]["id"], updates=[{"variable_name": "not_a_variable"}]
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "job.unknown_variable"


def test_quality_report_404_before_loading(client, admin_factory, synthetic_dta):
    admin = admin_factory()
    headers = admin_headers(admin)
    body = upload_survey(client, headers, synthetic_dta).json()
    response = client.get(
        f"{API}/admin/surveys/{body['survey']['id']}/quality-report", headers=headers
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "quality_report.not_found"


def test_missing_file_fails_the_job_with_a_clear_code(client, admin_factory, db):
    from app.analysis import pipeline
    from app.config import get_settings
    from app.models.enums import JobStatus, SurveyStatus
    from app.models.survey import SurveyJob

    admin = admin_factory()
    headers = admin_headers(admin)
    survey = Survey(
        name="Gone file",
        year=2022,
        original_filename="gone.dta",
        stored_path="gone/gone.dta",
        file_size_bytes=10,
        checksum_sha256="0" * 64,
        uploaded_by_id=admin.id,
        status=SurveyStatus.UPLOADED,
    )
    db.add(survey)
    db.commit()
    job = SurveyJob(survey_id=survey.id, status=JobStatus.QUEUED)
    db.add(job)
    db.commit()

    pipeline.run_job(job.id, get_settings())

    detail = client.get(f"{API}/admin/jobs/{job.id}", headers=headers).json()
    assert detail["status"] == "failed"
    assert detail["error_code"] == "survey.file_missing"
    assert detail["error_translation_key"] == "errors.survey.file_missing"
    assert detail["events"][-1]["type"] == "job.failed"

    survey_status = client.get(f"{API}/admin/surveys/{survey.id}", headers=headers).json()
    assert survey_status["status"] == "failed"


def test_quality_report_explains_repeated_household_rows(client, admin_factory, synthetic_dta):
    admin = admin_factory()
    headers = admin_headers(admin)
    body = upload_survey(client, headers, synthetic_dta).json()
    confirm_mapping(client, headers, body["job"]["id"])

    report = client.get(
        f"{API}/admin/surveys/{body['survey']['id']}/quality-report", headers=headers
    ).json()

    assert report["household_rows_read"] > report["households_loaded"]
    assert (
        report["household_rows_read"] - report["duplicate_households"]
        == report["households_loaded"]
    )

    collapsed = next(
        item
        for item in report["rule_violations"]
        if item["rule"] == "repeated_household_rows_collapsed"
    )
    assert collapsed["count"] == report["duplicate_households"]
    assert "person row" in collapsed["detail"]
