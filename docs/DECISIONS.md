# DECISIONS (Consolidated Architecture Decisions)

This document consolidates high-level architectural decisions that shape the Fireball project. Each entry captures the decision, the rationale, alternatives considered, consequences, and the date. ADR (Architecture Decision Records) are used for each substantive decision and are kept in the docs/adr/ directory.

Latest ADRs referenced from this document:
- ipc-design.md
- jit-design.md
- hal-interface.md
- subsystem-services.md

Decision history
- IPC protocol consolidation and ADR-based governance
  - Rationale: Avoid duplication of decision history across IPC-related specs; provide a single source of truth for IPC design.
  - Consequences: Specifications reference ADRs; ADRs are the canonical source for historical decisions.

- JIT design consolidation
  - Rationale: Separate ADR for JIT decisions to isolate from IPC and other subsystems.
  - Consequences: Documentation references JIT ADRs; future JIT changes are captured in ADRs.

- HAL interface consolidation
  - Rationale: Centralize decisions about HAL interfaces and platform integration.
  - Consequences: HAL-related decisions are tracked in ADRs and referenced from specs.

- Subsystem-Services ADR consolidation
  - Rationale: Consolidate decisions about Subsystems vs Services design into ADRs.
  - Consequences: ADRs provide rationale and alternatives for service partitioning and IPC usage.

Process and governance
- ADRs live under docs/adr/ and describe one decision per document with context, alternatives, rationale, consequences, and date.
- Specifications should reference relevant ADRs and minimize embedded historical narrative.
- Changes should be reviewed and committed with a clear ADR reference, ensuring traceability.

Next steps
- Create and populate ADRs for jit-design.md, hal-interface.md, subsystem-services.md (already in plan).
- Update each specification to include an ADR reference section with links to the corresponding ADR documents.
- Create or update docs/DECISIONS.md to include the latest consolidated decisions and links to ADRs.
- Consider a lightweight automation to generate ADR references from new decisions in the future.

Notes
- This page is intended to be lightweight and referential; it should not duplicate detailed decision content found in the ADRs themselves.
