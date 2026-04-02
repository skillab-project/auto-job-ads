# SKILLAB Auto Job Ads Service

A FastAPI microservice that generates high-quality job advertisements with an LLM.

The service is designed for industrial stakeholders who want structured, role-specific job ad drafts based on:
- company sector
- selected job role
- optional company and vacancy context
- optional past advertisements
- formatting and quality constraints

It returns both:
- a publication-ready full job advertisement
- a structured export with title, summary, responsibilities, qualifications, benefits, keywords, and call to action

---

## Features

- LLM-based job ad generation
- Structured JSON output enforced through a response schema
- Support for past advertisements as reference material
- Formatting controls such as language, tone, max words, and section order
- Quality controls such as required content, avoided content, and minimum list sizes
- Synchronous and asynchronous job-based API flows

---

## Project Structure

```text
.
|-- app/
|   |-- main.py
|-- .env.example
|-- Dockerfile
|-- docker-compose.yml
|-- requirements.txt
`-- README.md
```

---

## Requirements

- Python 3.11+
- An LLM endpoint compatible with `POST /api/chat/completions`

---

## Environment Variables

Create a `.env` file with:

```env
API_URL=http://localhost:3000
API_TOKEN=your_token_here
MODEL_NAME=mistral:latest
TEMPERATURE=0.1
SEED=42
TIMEOUT=60
```

Notes:
- `API_URL` should point to your LLM service base URL.
- The service sends requests to `API_URL/api/chat/completions`.

---

## Installation

```bash
pip install -r requirements.txt
```

Run locally:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8891
```

Swagger UI:

`http://localhost:8891/docs`

---

## Docker

Build:

```bash
docker build -t auto-job-ads .
```

Run:

```bash
docker run --env-file .env -p 8891:8891 auto-job-ads
```

---

## API

### `POST /job-ad/generate`

Generates a job advertisement synchronously.

Example request:

```json
{
  "company_sector": "Industrial Manufacturing",
  "job_role": "Maintenance Technician",
  "company_name": "Acme Industries",
  "location": "Athens, Greece",
  "employment_type": "Full-time",
  "seniority": "Mid-level",
  "work_model": "On-site",
  "team_context": "Join the plant operations team supporting preventive and corrective maintenance across production lines.",
  "additional_context": "Experience with PLC-supported equipment is valuable.",
  "past_advertisements": [
    {
      "title": "Maintenance Technician",
      "text": "Maintain production equipment, support shift uptime, and coordinate with engineering teams.",
      "notes": "Keep the tone practical and concise."
    }
  ],
  "formatting_constraints": {
    "language": "English",
    "tone": "Professional and inclusive",
    "max_words": 650,
    "section_order": [
      "Job title",
      "Role summary",
      "Key responsibilities",
      "Required qualifications",
      "Preferred qualifications",
      "Benefits",
      "Call to action"
    ],
    "output_format": "markdown"
  },
  "quality_constraints": {
    "must_include": [
      "health and safety awareness",
      "preventive maintenance"
    ],
    "avoid": [
      "rockstar",
      "ninja"
    ],
    "min_responsibilities": 6,
    "min_requirements": 5,
    "min_benefits": 3
  }
}
```

Example response:

```json
{
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
    "summary": "We are looking for a Maintenance Technician to support equipment reliability and safe plant operations.",
    "responsibilities": [
      "Perform preventive maintenance on production equipment.",
      "Diagnose and resolve mechanical and electrical faults.",
      "Support line uptime by responding to breakdowns quickly.",
      "Follow health and safety procedures during all maintenance activities.",
      "Record maintenance work accurately in the relevant systems.",
      "Collaborate with engineering and production teams to improve reliability."
    ],
    "required_qualifications": [
      "Experience in industrial equipment maintenance.",
      "Ability to read technical manuals and maintenance instructions.",
      "Knowledge of preventive maintenance practices.",
      "Awareness of workplace health and safety requirements.",
      "Ability to troubleshoot equipment issues in a production environment."
    ],
    "preferred_qualifications": [
      "Experience with PLC-supported machinery."
    ],
    "benefits": [
      "Stable full-time employment.",
      "Training and development opportunities.",
      "Supportive team environment."
    ],
    "call_to_action": "Apply now to join our operations team.",
    "keywords": [
      "maintenance technician",
      "preventive maintenance",
      "industrial manufacturing",
      "equipment reliability",
      "health and safety"
    ],
    "full_advertisement": "# Maintenance Technician\n\n..."
  }
}
```

### `POST /jobs/job-ad`

Starts asynchronous job-ad generation.

Example response:

```json
{
  "job_id": "c4a1f90e-5f17-4ac3-9fb9-2a408bd54042",
  "status_url": "http://localhost:8891/jobs/c4a1f90e-5f17-4ac3-9fb9-2a408bd54042"
}
```

### `GET /jobs/{job_id}`

Poll job status.

Running:

```json
{
  "status": "running",
  "result": null,
  "error": null
}
```

Success:

```json
{
  "status": "success",
  "result": {
    "prompt_summary": "Create a high-quality job advertisement for the role 'Maintenance Technician' in the 'Industrial Manufacturing' sector.",
    "structured_export": {
      "title": "Maintenance Technician"
    }
  },
  "error": null
}
```

### `GET /health`

Returns service health and the configured model.

Example response:

```json
{
  "status": "ok",
  "model": "mistral:latest"
}
```

---

## Typical Flow

### Synchronous

1. Send job-ad inputs to `POST /job-ad/generate`
2. The service constructs an LLM prompt from sector, role, context, past ads, and constraints
3. The LLM returns structured JSON
4. The service validates and returns the final result

### Asynchronous

1. Send the same payload to `POST /jobs/job-ad`
2. Receive a `job_id`
3. Poll `GET /jobs/{job_id}`
4. Read the generated job ad when `status` becomes `success`
