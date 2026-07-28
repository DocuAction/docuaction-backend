# ADR-003: PostgreSQL as the Primary Datastore

**Alliance Global Tech, Inc. (AGT)** — Copyright © 2024–2026
**Status:** Accepted
**Deciders:** DocuAction Engineering (@imran-agt)
**Applies to:** DocuAction AI backend (Version 6.0.0)

---

## Context

DocuAction AI persists structured, relational data — users and RBAC roles, cases,
documents, claims, TEFCA review records, audit logs, and more — with strong
integrity, transactional guarantees, and schema evolution over time. As a regulated
workload handling PHI/PII/CUI, the datastore must support encryption, managed
operations, and auditable change control, and it must integrate cleanly with the
async FastAPI application.

## Decision

We selected **PostgreSQL**, hosted as **Azure Database for PostgreSQL Flexible
Server**, accessed asynchronously:

- **SQLAlchemy 2.0.35 (async)** as the ORM.
- **asyncpg 0.29.0** as the async driver.
- **Alembic 1.13.2** for versioned, reviewable schema migrations.

## Consequences

### Positive

- **Relational integrity** — ACID transactions, foreign keys, and constraints
  protect data consistency across modules.
- **Async performance** — asyncpg pairs with FastAPI's event loop for efficient,
  non-blocking database access.
- **Managed operations** — Flexible Server provides backups, patching, high
  availability options, and encryption at rest, reducing operational burden.
- **Controlled schema evolution** — Alembic migrations are versioned, peer-reviewed,
  and auditable, supporting change-management controls.
- **Mature ecosystem** — rich SQL feature set (JSONB, indexing, full-text) supports
  diverse module needs.

### Negative

- **Async migration nuance** — connection URLs and SSL settings differ from
  synchronous drivers and must be configured correctly per environment.
- **Operational tuning** — connection pooling and Flexible Server sizing require
  attention under load.

## Alternatives Considered

- **MySQL / MariaDB** — capable, but PostgreSQL offers richer types (JSONB),
  stronger standards compliance, and better fit with our tooling.
- **NoSQL (MongoDB, etc.)** — rejected: the domain is strongly relational and
  benefits from transactional integrity and constraints.
- **Self-managed PostgreSQL on VMs** — rejected: higher operational and compliance
  burden than the managed Flexible Server.
