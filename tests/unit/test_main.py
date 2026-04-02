from pathlib import Path
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import main


def _sample_request(**overrides):
    payload = {
        "company_sector": "Industrial Manufacturing",
        "job_role": "Maintenance Technician",
        "company_name": "Acme Industries",
        "location": "Athens, Greece",
        "employment_type": "Full-time",
        "seniority": "Mid-level",
        "work_model": "On-site",
        "team_context": "  Support    plant uptime and maintenance planning. ",
        "additional_context": " PLC experience is useful. ",
        "past_advertisements": [
            {
                "title": "  Previous Technician Ad ",
                "text": " Maintain   equipment and support operations. ",
                "notes": " Keep it concise. ",
            }
        ],
        "formatting_constraints": {
            "language": "English",
            "tone": "Professional and inclusive",
            "max_words": 500,
            "section_order": [
                "Job title",
                "Role summary",
                "Key responsibilities",
            ],
            "output_format": "markdown",
        },
        "quality_constraints": {
            "must_include": ["preventive maintenance"],
            "avoid": ["rockstar"],
            "min_responsibilities": 6,
            "min_requirements": 5,
            "min_benefits": 3,
        },
    }
    payload.update(overrides)
    return payload


def _sample_llm_response():
    return {
        "prompt_summary": "Create a high-quality job advertisement for the role 'Maintenance Technician' in the 'Industrial Manufacturing' sector.",
        "structured_export": {
            "title": "Maintenance Technician",
            "sector": "Industrial Manufacturing",
            "role": "Maintenance Technician",
            "language": "English",
            "tone": "Professional and inclusive",
            "seniority": "Mid-level",
            "location": "Athens, Greece",
            "employment_type": "Full-time",
            "work_model": "On-site",
            "summary": "Support equipment uptime and safe operations.",
            "responsibilities": [
                "Perform preventive maintenance.",
                "Respond to breakdowns.",
                "Inspect equipment condition.",
                "Document maintenance work.",
                "Coordinate with production teams.",
                "Follow safety procedures.",
            ],
            "required_qualifications": [
                "Maintenance experience.",
                "Troubleshooting skills.",
                "Technical reading ability.",
                "Safety awareness.",
                "Industrial environment experience.",
            ],
            "preferred_qualifications": [
                "PLC exposure.",
            ],
            "benefits": [
                "Stable role.",
                "Training opportunities.",
                "Supportive team.",
            ],
            "call_to_action": "Apply now.",
            "keywords": [
                "maintenance technician",
                "preventive maintenance",
                "industrial manufacturing",
                "equipment reliability",
                "health and safety",
            ],
            "full_advertisement": "# Maintenance Technician\n\nApply now.",
        },
    }


def test_compact_text_normalizes_whitespace():
    assert main._compact_text("  hello   world \n\t again ") == "hello world again"
    assert main._compact_text("   ") is None
    assert main._compact_text(None) is None


def test_serialize_past_ads_compacts_optional_fields():
    ads = [
        main.PastAdvertisement(
            title="  Old Ad  ",
            text="  Line one \n line two  ",
            notes="  Keep concise  ",
        ),
        main.PastAdvertisement(text="Simple"),
    ]

    assert main._serialize_past_ads(ads) == [
        {"title": "Old Ad", "text": "Line one line two", "notes": "Keep concise"},
        {"text": "Simple"},
    ]


def test_call_llm_for_job_ad_builds_expected_payload(monkeypatch):
    captured = {}

    def fake_chat(payload, timeout):
        captured["payload"] = payload
        captured["timeout"] = timeout
        return _sample_llm_response()

    monkeypatch.setattr(main, "_chat_json", fake_chat)

    result = main.call_llm_for_job_ad(main.JobAdRequest(**_sample_request()))

    assert result["structured_export"]["title"] == "Maintenance Technician"
    assert captured["timeout"] == main.settings.timeout + 10
    assert captured["payload"]["model"] == main.settings.model
    assert captured["payload"]["format"]["properties"]["structured_export"]["properties"]["responsibilities"]["minItems"] == 6

    message = captured["payload"]["messages"][0]["content"]
    assert "Company Sector: Industrial Manufacturing" in message
    assert 'Preferred Section Order: ["Job title", "Role summary", "Key responsibilities"]' in message
    assert '"title": "Previous Technician Ad"' in message
    assert '"text": "Maintain equipment and support operations."' in message
    assert 'Must Include: ["preventive maintenance"]' in message
    assert 'Avoid: ["rockstar"]' in message


def test_call_llm_for_job_ad_sets_default_prompt_summary(monkeypatch):
    def fake_chat(payload, timeout):
        return {"structured_export": _sample_llm_response()["structured_export"]}

    monkeypatch.setattr(main, "_chat_json", fake_chat)

    result = main.call_llm_for_job_ad(main.JobAdRequest(**_sample_request()))

    assert result["prompt_summary"] == (
        "Create a high-quality job advertisement for the role "
        "'Maintenance Technician' in the 'Industrial Manufacturing' sector."
    )


def test_generate_job_ad_endpoint_returns_structured_response(monkeypatch):
    monkeypatch.setattr(main, "call_llm_for_job_ad", lambda req: _sample_llm_response())
    client = TestClient(main.app)

    response = client.post("/job-ad/generate", json=_sample_request())

    assert response.status_code == 200
    body = response.json()
    assert body["structured_export"]["role"] == "Maintenance Technician"
    assert len(body["structured_export"]["responsibilities"]) == 6


def test_generate_job_ad_endpoint_returns_502_on_llm_failure(monkeypatch):
    def fake_call(req):
        raise RuntimeError("backend unavailable")

    monkeypatch.setattr(main, "call_llm_for_job_ad", fake_call)
    client = TestClient(main.app)

    response = client.post("/job-ad/generate", json=_sample_request())

    assert response.status_code == 502
    assert "backend unavailable" in response.json()["detail"]


def test_run_job_ad_job_stores_success_result(monkeypatch):
    monkeypatch.setattr(main, "call_llm_for_job_ad", lambda req: _sample_llm_response())
    job_id = "job-success"
    main.JOB_STORE[job_id] = {"status": "pending", "result": None, "error": None}

    main.run_job_ad_job(job_id, main.JobAdRequest(**_sample_request()))

    assert main.JOB_STORE[job_id]["status"] == "success"
    assert main.JOB_STORE[job_id]["result"]["structured_export"]["title"] == "Maintenance Technician"
    assert main.JOB_STORE[job_id]["error"] is None


def test_run_job_ad_job_stores_error_result(monkeypatch):
    def fake_call(req):
        raise RuntimeError("llm timeout")

    monkeypatch.setattr(main, "call_llm_for_job_ad", fake_call)
    job_id = "job-error"
    main.JOB_STORE[job_id] = {"status": "pending", "result": None, "error": None}

    main.run_job_ad_job(job_id, main.JobAdRequest(**_sample_request()))

    assert main.JOB_STORE[job_id]["status"] == "error"
    assert main.JOB_STORE[job_id]["result"] is None
    assert main.JOB_STORE[job_id]["error"] == "llm timeout"
