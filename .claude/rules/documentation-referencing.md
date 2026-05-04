name: documentation-referencing
instructions: |
  1. Documentation is Authoritative: Before implementing or refactoring any component, read the corresponding document in `docs/components/`.
  2. Maintain Traceability: All architectural decisions must trace back to the requirements specified in the design documents (marked with `{Keyword}`).
  3. Keep Docs Updated: If you find code that deviates from its design document, either fix the code OR update the document (after consulting the user).
  4. Context Lookup: When working on a module, verify its Tier (1, 2, or 3) and apply the associated constraints first.
---
This rule ensures that the implementation stays strictly aligned with the Fireball project design documents.
