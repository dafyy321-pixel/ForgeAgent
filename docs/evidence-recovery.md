# Evidence-driven recovery

Enable `harness.evidence_recovery` on a new task, or select “启用证据驱动恢复” in the task creation advanced settings. It defaults to false for existing clients. Apply migrations through `0010` before starting the API and worker.

The worker records immutable, tenant/run-bound evidence after failed tools or independent acceptance. A separate durable quantum classifies the evidence, records a diagnosis and selects a bounded recovery. Acceptance failure establishes a failed contract, not a proven implementation root cause. Such diagnoses retain implementation/target-understanding/specification hypotheses. Inconclusive results retain unknowns and pause unless a reviewed infrastructure error supports an observation retry.

Code repair requires a successful public source/evidence read before workspace writes, enforced at preparation and dispatch. Environment/tool recovery retries only reviewed read operations or the same frozen verification. Hard constraints, unresolved effects, billing uncertainty, cancellation and authorization gates retain their existing authority. Repeated failure without changed workspace/evidence stops. Final success always requires independent acceptance.

`max_diagnoses`, `max_recovery_attempts` and existing `max_repair_attempts` bound the policy, including aggregate root/child consumption. Root wall/CPU/storage/tool quotas still apply. Input revisions and explicit runtime migrations supersede outstanding recovery plans without resetting counters. Old runs retain archived executor semantics.

`GET /v1/runs/{id}/recovery?kind=diagnoses|evidence|attempts` provides authorized, cursor-paginated history. The recovery tab displays evidence, hypotheses, unknowns, strategies and outcomes. Research snapshots report diagnostic recovery separately from lease recovery. Backups, event projections and registered knowledge erasure include the new records.

Current policy is deterministic. It does not claim to identify every root cause, automatically rewrite specifications, install dependencies or repair arbitrary infrastructure. Mechanism tests establish boundaries and persistence; model effectiveness requires paired evaluations with fixed tasks, model and total budgets.
