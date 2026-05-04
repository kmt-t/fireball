name: documentation-validation
instructions: |
  1. Automated Validation: Before committing code, run the cross-sectional validation script to ensure all new/modified code is backed by an entry in `docs/components/`.
  2. Keyword Verification: Verify that the implemented logic carries the required keyword annotations (e.g., `{CooperativeMultitasking}`) as specified in the relevant component design document.
  3. Tier Enforcement: Validate that code residing in Tier 3 (Implementation Domain) does not violate restrictions related to Tier 1 or Tier 2 modules (e.g., direct hardware access from Tier 3 without going through HAL).
  4. Traceability Check: Every architectural change must clearly trace to a requirement in a design document.
---
This rule ensures that the implementation stays strictly aligned with the Fireball project design documents via automated keyword validation.
