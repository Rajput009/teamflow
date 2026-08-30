"""AI layer package — see docs/features/11-ai-task-generator.md.

Architecture invariants:
- The LLM never touches the database; it produces validated proposals.
- Accept endpoints treat proposals as untrusted input and re-run all
  business rules through the normal services.
- Every accepted proposal is audited with the human approver as actor.
"""
