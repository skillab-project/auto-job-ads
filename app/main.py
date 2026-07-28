# -*- coding: utf-8 -*-
"""
Created on Thu Apr 2 13:33:15 2026

@author: tsoukj
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

import requests
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, ValidationError


# =========================
# Config (env-based)
# =========================
load_dotenv()


class Settings(BaseModel):
    api_url: str = os.getenv("API_URL", "<REPLACE_ME>")
    api_token: str = os.getenv("API_TOKEN", "<REPLACE_ME>")
    model: str = os.getenv("MODEL_NAME", "mistral:latest")
    temperature: float = float(os.getenv("TEMPERATURE", "0.1"))
    seed: int = int(os.getenv("SEED", "42"))
    timeout: int = int(os.getenv("TIMEOUT", "60"))


settings = Settings()

logger = logging.getLogger("auto-job-ads")

HEADERS = {
    "Authorization": f"Bearer {settings.api_token}",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "SKILLAB-Job-Ads/1.0",
}

JOB_STORE: Dict[str, Dict[str, Any]] = {}

RESULTS_DIR = Path(os.getenv("RESULTS_DIR", "./results"))
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


# =========================
# Result persistence
# =========================
def _result_path(job_id: str) -> Path:
    return RESULTS_DIR / f"{job_id}.json"


def _save_result(record: Dict[str, Any]) -> None:
    """Persist a job record to ./results/{id}.json."""
    job_id = record.get("job_id")
    if not job_id:
        return
    try:
        path = _result_path(job_id)
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        logger.error("Failed to persist result for job %s: %s", job_id, exc)


def _load_result(job_id: str) -> Optional[Dict[str, Any]]:
    """Load a persisted job record, or None if the file is missing/unreadable."""
    path = _result_path(job_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Failed to read result file for job %s: %s", job_id, exc)
        return None


# =========================
# Schemas
# =========================
class PastAdvertisement(BaseModel):
    title: Optional[str] = None
    text: str
    notes: Optional[str] = None


class FormattingConstraints(BaseModel):
    language: str = "English"
    tone: str = "Professional and inclusive"
    max_words: Optional[int] = None
    section_order: Optional[List[str]] = None
    output_format: str = "markdown"


class QualityConstraints(BaseModel):
    must_include: Optional[List[str]] = None
    avoid: Optional[List[str]] = None
    min_responsibilities: int = 6
    min_requirements: int = 5
    min_benefits: int = 3


class JobAdRequest(BaseModel):
    company_sector: str
    job_role: str
    company_name: Optional[str] = None
    location: Optional[str] = None
    employment_type: Optional[str] = None
    seniority: Optional[str] = None
    work_model: Optional[str] = None
    team_context: Optional[str] = None
    additional_context: Optional[str] = None
    past_advertisements: Optional[List[PastAdvertisement]] = None
    formatting_constraints: Optional[FormattingConstraints] = None
    quality_constraints: Optional[QualityConstraints] = None


class StructuredJobAdvertisement(BaseModel):
    title: str
    sector: str
    role: str
    language: str
    tone: str
    seniority: Optional[str] = None
    location: Optional[str] = None
    employment_type: Optional[str] = None
    work_model: Optional[str] = None
    summary: str
    responsibilities: List[str]
    required_qualifications: List[str]
    preferred_qualifications: List[str]
    benefits: List[str]
    call_to_action: str
    keywords: List[str]
    full_advertisement: str


class JobAdResponse(BaseModel):
    prompt_summary: str
    structured_export: StructuredJobAdvertisement


# =========================
# LLM client
# =========================
def _strip_code_fences(text: str) -> str:
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9]*\n?", "", text, count=1, flags=re.MULTILINE)
        text = re.sub(r"\n?```$", "", text, count=1, flags=re.MULTILINE)
    return text


def _stream_content(response: requests.Response) -> str:
    """Accumulate the assistant message content from an SSE stream."""
    parts: List[str] = []
    for raw_line in response.iter_lines(decode_unicode=True):
        if not raw_line:
            continue
        line = raw_line.strip()
        if line.startswith("data:"):
            line = line[len("data:"):].strip()
        if not line or line == "[DONE]":
            continue
        try:
            chunk = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            choice = chunk["choices"][0]
        except (KeyError, IndexError):
            continue
        # OpenAI-style streaming uses "delta"; some servers send "message".
        piece = (choice.get("delta") or choice.get("message") or {}).get("content")
        if piece:
            parts.append(piece)
    return "".join(parts)


def _chat_json(payload: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    url = f"{settings.api_url}/api/chat/completions"
    stream_payload = {**payload, "stream": True}
    for attempt in range(3):
        try:
            response = requests.post(
                url,
                headers=HEADERS,
                json=stream_payload,
                timeout=timeout,
                stream=True,
            )
            response.raise_for_status()
            content = _stream_content(response)
            if not content.strip():
                raise RuntimeError("LLM returned an empty stream")
            return json.loads(_strip_code_fences(content))
        except Exception as exc:
            logger.warning("LLM request failed (attempt %d/3): %s", attempt + 1, exc)
            if attempt == 2:
                raise
            time.sleep(0.75 * (attempt + 1))
    raise RuntimeError("Unreachable")


def _compact_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", value).strip()
    return cleaned or None


def _serialize_past_ads(past_ads: Optional[List[PastAdvertisement]]) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    for ad in past_ads or []:
        item: Dict[str, str] = {"text": _compact_text(ad.text) or ""}
        if ad.title:
            item["title"] = _compact_text(ad.title) or ""
        if ad.notes:
            item["notes"] = _compact_text(ad.notes) or ""
        items.append(item)
    return items


def call_llm_for_job_ad(req: JobAdRequest) -> Dict[str, Any]:
    formatting = req.formatting_constraints or FormattingConstraints()
    quality = req.quality_constraints or QualityConstraints()
    past_ads = _serialize_past_ads(req.past_advertisements)

    section_order = formatting.section_order or [
        "Job title",
        "Role summary",
        "Key responsibilities",
        "Required qualifications",
        "Preferred qualifications",
        "Benefits",
        "Call to action",
    ]

    prompt_summary = (
        f"Create a high-quality job advertisement for the role '{req.job_role}' "
        f"in the '{req.company_sector}' sector."
    )

    system_block = (
        "You are an expert talent acquisition writer supporting industrial stakeholders.\n"
        "Generate a polished, credible, and inclusive job advertisement tailored to the target role.\n"
        "Balance specificity with clarity, avoid generic filler, and make the responsibilities realistic for the role and sector.\n"
        "Return only valid JSON that matches the provided schema."
    )

    user_lines = [
        f"- Company Sector: {req.company_sector}",
        f"- Job Role: {req.job_role}",
        f"- Company Name: {req.company_name or 'Not provided'}",
        f"- Location: {req.location or 'Not provided'}",
        f"- Employment Type: {req.employment_type or 'Not provided'}",
        f"- Seniority: {req.seniority or 'Not provided'}",
        f"- Work Model: {req.work_model or 'Not provided'}",
        f"- Team Context: {_compact_text(req.team_context) or 'Not provided'}",
        f"- Additional Context: {_compact_text(req.additional_context) or 'Not provided'}",
        f"- Language: {formatting.language}",
        f"- Tone: {formatting.tone}",
        f"- Output Format: {formatting.output_format}",
        f"- Preferred Section Order: {json.dumps(section_order, ensure_ascii=False)}",
        f"- Max Words: {formatting.max_words if formatting.max_words is not None else 'No strict limit'}",
        f"- Must Include: {json.dumps(quality.must_include or [], ensure_ascii=False)}",
        f"- Avoid: {json.dumps(quality.avoid or [], ensure_ascii=False)}",
        f"- Minimum Responsibilities: {quality.min_responsibilities}",
        f"- Minimum Requirements: {quality.min_requirements}",
        f"- Minimum Benefits: {quality.min_benefits}",
        f"- Relevant Past Advertisements: {json.dumps(past_ads, ensure_ascii=False)}",
    ]

    rules = (
        "Task:\n"
        "1. Build a role-specific job advertisement for this vacancy.\n"
        "2. Use any past advertisements as style/context references, but do not copy them verbatim.\n"
        "3. Ensure the responsibilities are concrete, sector-aware, and aligned with the selected role.\n"
        "4. Ensure the qualification lists are realistic and not inflated.\n"
        "5. The `full_advertisement` field must be publication-ready and follow the requested section order.\n"
        "6. Keep the language inclusive and professional.\n"
        "7. Return exactly these fields inside `structured_export`:\n"
        "   - title\n"
        "   - sector\n"
        "   - role\n"
        "   - language\n"
        "   - tone\n"
        "   - seniority\n"
        "   - location\n"
        "   - employment_type\n"
        "   - work_model\n"
        "   - summary\n"
        "   - responsibilities\n"
        "   - required_qualifications\n"
        "   - preferred_qualifications\n"
        "   - benefits\n"
        "   - call_to_action\n"
        "   - keywords\n"
        "   - full_advertisement\n"
        "Output JSON format (return ONLY valid JSON):\n"
        "{\n"
        '  "prompt_summary": "...",\n'
        '  "structured_export": {\n'
        '    "title": "...",\n'
        '    "sector": "...",\n'
        '    "role": "...",\n'
        '    "language": "...",\n'
        '    "tone": "...",\n'
        '    "seniority": "...",\n'
        '    "location": "...",\n'
        '    "employment_type": "...",\n'
        '    "work_model": "...",\n'
        '    "summary": "...",\n'
        '    "responsibilities": ["..."],\n'
        '    "required_qualifications": ["..."],\n'
        '    "preferred_qualifications": ["..."],\n'
        '    "benefits": ["..."],\n'
        '    "call_to_action": "...",\n'
        '    "keywords": ["..."],\n'
        '    "full_advertisement": "..."\n'
        "  }\n"
        "}"
    )

    payload = {
        "model": settings.model,
        "messages": [
            {
                "role": "user",
                "content": (
                    f"System instructions:\n{system_block}\n\n"
                    f"Job input:\n" + "\n".join(user_lines) + "\n\n"
                    f"Task and Output Rules:\n{rules}"
                ),
            }
        ],
        "temperature": settings.temperature,
        "seed": settings.seed,
        "format": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "prompt_summary": {"type": "string"},
                "structured_export": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "sector": {"type": "string"},
                        "role": {"type": "string"},
                        "language": {"type": "string"},
                        "tone": {"type": "string"},
                        "seniority": {"type": ["string", "null"]},
                        "location": {"type": ["string", "null"]},
                        "employment_type": {"type": ["string", "null"]},
                        "work_model": {"type": ["string", "null"]},
                        "summary": {"type": "string"},
                        "responsibilities": {
                            "type": "array",
                            "minItems": quality.min_responsibilities,
                            "items": {"type": "string"},
                        },
                        "required_qualifications": {
                            "type": "array",
                            "minItems": quality.min_requirements,
                            "items": {"type": "string"},
                        },
                        "preferred_qualifications": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "benefits": {
                            "type": "array",
                            "minItems": quality.min_benefits,
                            "items": {"type": "string"},
                        },
                        "call_to_action": {"type": "string"},
                        "keywords": {
                            "type": "array",
                            "minItems": 5,
                            "items": {"type": "string"},
                        },
                        "full_advertisement": {"type": "string"},
                    },
                    "required": [
                        "title",
                        "sector",
                        "role",
                        "language",
                        "tone",
                        "summary",
                        "responsibilities",
                        "required_qualifications",
                        "preferred_qualifications",
                        "benefits",
                        "call_to_action",
                        "keywords",
                        "full_advertisement",
                    ],
                    "additionalProperties": False,
                },
            },
            "required": ["prompt_summary", "structured_export"],
            "additionalProperties": False,
        },
    }

    raw = _chat_json(payload, timeout=settings.timeout + 10)
    raw.setdefault("prompt_summary", prompt_summary)
    return raw


# =========================
# Background jobs
# =========================
def run_job_ad_job(job_id: str, req: JobAdRequest) -> None:
    record = JOB_STORE[job_id]
    record["organization"] = req.company_name
    try:
        record["status"] = "running"
        raw = call_llm_for_job_ad(req)
        record["status"] = "success"
        record["result"] = JobAdResponse(**raw).dict()
    except Exception as exc:
        record["status"] = "error"
        record["error"] = str(exc)
    finally:
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        _save_result(record)


# =========================
# FastAPI app
# =========================
app = FastAPI(title="SKILLAB Auto Job Ads Service", version="1.0.0")


@app.post("/jobs/job-ad")
def create_job_ad_job(req: JobAdRequest, background: BackgroundTasks, request: Request):
    job_id = str(uuid4())
    JOB_STORE[job_id] = {
        "job_id": job_id,
        "status": "pending",
        "result": None,
        "error": None,
        "organization": req.company_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    background.add_task(run_job_ad_job, job_id, req)
    return {"job_id": job_id, "status_url": f"{request.base_url}jobs/{job_id}"}


@app.get("/jobs")
def list_jobs(organization: str = Query(..., description="Organization / company name")):
    """Return all job ids that have a persisted result for the given organization."""
    job_ids: List[str] = []
    for path in RESULTS_DIR.glob("*.json"):
        record = _load_result(path.stem)
        if record and record.get("organization") == organization:
            job_ids.append(record.get("job_id", path.stem))
    return {"organization": organization, "count": len(job_ids), "job_ids": job_ids}


@app.get("/jobs/{job_id}")
def get_job_status(job_id: str):
    if job_id in JOB_STORE:
        return JOB_STORE[job_id]
    record = _load_result(job_id)
    if record is not None:
        return record
    raise HTTPException(status_code=404, detail="Job ID not found")


@app.post("/job-ad/generate", response_model=JobAdResponse)
def generate_job_ad(req: JobAdRequest):
    try:
        raw = call_llm_for_job_ad(req)
        return JobAdResponse(**raw)
    except ValidationError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Invalid LLM JSON schema for job advertisement: {exc}",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"LLM job advertisement generation failed: {exc}",
        )


@app.get("/health")
def health():
    return {"status": "ok", "model": settings.model}
