# MDoNER Service A

Service A is a deterministic memory-assistance calling companion. This repository
contains the local conversation engine, question planning, speech answer
evaluation, safety handling, Twilio webhook adapter, outbound-call trigger,
local call/session metrics, and mock testing.

It is intended for local deterministic development, testing, and controlled
prototype use before integration into the main MDoNER project.

## Repository Structure

```text
app/
  main.py              FastAPI application and webhook routes
  mock_call.py         Conversation engine and mock call runner
  question_planner.py  Deterministic question planning interfaces
  persistence.py       Local repository and SQLite persistence
  config.py            Environment-based configuration
  telephony/           Mock and Twilio adapters
tests/                 Unit and integration-style tests
pyproject.toml         Package metadata and dependencies
requirements.txt       Editable install with test dependencies
```

## Requirements

Python **3.11 or newer** is required.

## Local Setup

From the repository root in Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

The FastAPI application entry point is `app.main:app`. Start it locally with:

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Health check:

```text
GET http://127.0.0.1:8000/health
```

Run the complete test suite with:

```powershell
python -m pytest
```

The full test suite currently passes.

## Local Call Interfaces

The mock call runner uses `MockTelephonyAdapter` with deterministic responses.
It exercises readiness handling, the conversation state machine, 5-8 question
plans, answer classifications, safety thresholds, persistence, and idempotency
without making external calls.

The real Twilio webhook routes are:

```text
POST /webhooks/twilio/voice/start
POST /webhooks/twilio/voice/readiness
POST /webhooks/twilio/voice/answer
```

Twilio signatures are validated on these webhook requests. The developer
outbound trigger is:

```text
POST /v1/calls/trigger
```

It accepts a JSON body containing an E.164 `to_phone_number`.

## Environment Variables

Configure these values locally, using `.env.example` as a template:

```text
TWILIO_ACCOUNT_SID
TWILIO_AUTH_TOKEN
TWILIO_FROM_PHONE_NUMBER
TWILIO_PUBLIC_BASE_URL
SERVICE_A_TRIGGER_AUTH_TOKEN
```

`SERVICE_A_TRIGGER_AUTH_TOKEN` protects `POST /v1/calls/trigger`. Send the
configured value in this header:

```text
X-Service-A-Trigger-Token: <configured-token>
```

Do not log or include the token in responses. The `.env` file contains secrets
and must never be committed.

## Persistence and Integration Boundary

The current implementation uses local SQLite persistence through the repository
abstraction. It is suitable for deterministic local development and controlled
prototype use, but it is not a shared multi-instance database and has local
filesystem durability limitations.

Patient integration, FamilyMemory integration, and shared Supabase/Postgres
persistence are deferred to integration with the main MDoNER project. This
repository currently uses local deterministic fixtures and interfaces for those
boundaries; it does not implement the shared models or storage.
