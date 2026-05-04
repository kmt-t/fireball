name: coding-standards
instructions: |
  1. Naming: No prefixes/postfixes. No trailing underscores in POD members (e.g., use `next`, not `next_`).
  2. Memory: Prohibit dynamic containers (std::list, std::vector). MUST use intrusive lists for tasks.
  3. Constraints: Design for RAM < 64KB. Keep components deterministic and stackless.
  4. Design: Tier 3 modules (like the scheduler) must be singletons injected using the Harness Pattern.
  5. Reviews: Before finishing an implementation turn, self-review against these rules.
type: coding_standards
---
This rule defines the strict coding standards for the Fireball project, emphasizing low-resource determinism and intrusive data structures.
