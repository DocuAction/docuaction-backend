# ADR-002: FastAPI as the Web Framework

**Alliance Global Tech, Inc. (AGT)** — Copyright © 2024–2026
**Status:** Accepted
**Deciders:** DocuAction Engineering (@imran-agt)
**Applies to:** DocuAction AI backend (Version 6.0.0)

---

## Context

The DocuAction AI backend exposes approximately 261 endpoints across many modules
and must handle I/O-bound workloads efficiently — external AI model calls, TEFCA
connector queries over HTTP, and database access. It requires strong input
validation for PHI/PII/CUI safety, machine-readable API documentation for
integrators and reviewers, and a modern async programming model.

## Decision

We adopted **FastAPI 0.115.0** (on Python 3.12, served by Uvicorn 0.30.6 under
gunicorn) as the web framework.

- **Asynchronous** request handling to efficiently overlap external AI, connector,
  and database I/O.
- **Pydantic 2.9.2** for declarative request/response validation and serialization,
  enforcing schema discipline at the boundary.
- **Automatic OpenAPI/Swagger** generation (gated behind `ENABLE_DOCS`) for
  discoverable, contract-first APIs.

## Consequences

### Positive

- **High throughput** for I/O-bound workloads via native async/await.
- **Strong validation** — Pydantic models reject malformed input early and reduce
  the risk of unsafe data reaching business logic.
- **Self-documenting API** — OpenAPI/Swagger supports integrators, testing, and
  compliance review.
- **Type-driven development** improves maintainability across ~261 endpoints.
- **Ecosystem fit** with SQLAlchemy async, httpx, and the Anthropic SDK.

### Negative

- **Async discipline required** — blocking calls must be avoided or offloaded to
  prevent event-loop stalls.
- **Newer ecosystem** than long-established synchronous frameworks; some libraries
  require async-aware alternatives.

## Alternatives Considered

- **Flask** — mature and simple, but synchronous by default and lacks built-in
  validation and OpenAPI generation.
- **Django REST Framework** — feature-rich but heavier and synchronous-first;
  more than needed for a service-oriented API layer.
- **Starlette (raw)** — FastAPI already builds on Starlette while adding the
  Pydantic validation and OpenAPI tooling we require.
