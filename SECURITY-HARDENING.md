# Security Hardening Strategy

This document proposes a path to make the password-rotation pipeline materially safer without losing the core product value:

- autonomous cross-site password changes,
- minimal per-site customization,
- practical throughput for large vaults,
- local-first operation.

The current system is already useful; this plan is about reducing blast radius and accidental exposure while keeping behavior and outcomes intact.

## Product Values to Preserve

Any hardening change should be evaluated against these constraints:

1. **Autonomy:** agent can still navigate unknown websites and complete end-to-end flows.
2. **Generality:** avoid brittle site-specific logic.
3. **Operator speed:** setup should remain lightweight for one-off migration projects.
4. **Observability:** outcomes still need clear status and triage data.
5. **Local control:** no mandatory cloud secret processor.

## Threats Worth Addressing First

- Credentials persisted in plaintext SQLite and logs.
- Prompt files containing usernames/passwords on disk.
- Broad process-level access if another local process is compromised.
- Long-lived sensitive artifacts after runs.
- Overly permissive shell and filesystem surface during agent execution.

## Hardening Plan (Phased)

## Phase 1: Fast Wins (Low Complexity, High Value)

### 1) Encrypted local secret store

- Keep metadata in `passwords.db` but move `old_password` and `new_password` into encrypted blobs.
- Use an envelope key derived from a passphrase entered at run start (memory-only key; never written to disk).
- Decrypt just-in-time per task, clear variables immediately after use.

**Retains value:** No impact on site coverage or agent autonomy; operator flow changes by one unlock prompt.

### 2) Secret-minimized logging

- Add log redaction rules before writing agent output:
  - mask probable password fields,
  - remove credential echoes from prompt snippets,
  - strip known secret markers.
- Store full raw logs only behind an explicit `DEBUG_SECRETS=1` flag.

**Retains value:** Keeps observability and failure triage while reducing accidental leakage.

### 3) Ephemeral prompt handling

- Stop writing full prompt files to disk by default.
- Pipe prompt content directly to the agent process (`stdin`) when possible.
- If prompt files are needed for debugging, write to a tmp location and auto-delete on completion.

**Retains value:** Agent behavior unchanged; fewer credential traces at rest.

### 4) Pre-commit secret scanning

- Add local git hook recommendations with tools like `gitleaks` or `detect-secrets`.
- Fail commits on high-confidence secrets unless explicitly approved.

**Retains value:** No runtime impact; reduces accidental publication risk.

## Phase 2: Runtime Containment (Medium Complexity)

### 5) Task-scoped execution sandbox

- Run each worker in a constrained environment:
  - dedicated temporary directory,
  - minimal env vars,
  - restricted writable paths.
- Limit network egress for non-essential endpoints when feasible.

**Retains value:** Browser automation still works, but compromise impact is narrowed.

### 6) Capability-based tool profile

- Split agent tools into profiles:
  - default profile: browser + minimal shell,
  - debug profile: extended shell/filesystem access.
- Require explicit opt-in to debug profile.

**Retains value:** Keeps autonomous navigation while reducing default privileges.

### 7) In-memory credential handoff

- Feed credentials via process memory (env fd / stdin / IPC) rather than command args or files.
- Never print credentials in orchestrator stdout.

**Retains value:** No reduction in automation quality; lower incidental exposure.

## Phase 3: Structural Upgrades (Higher Complexity)

### 8) Two-database model (metadata + secrets)

- Keep main `passwords.db` for task state and analytics.
- Move secret material to a separate encrypted database (`secrets.db.enc`) with strict access wrapper.

**Retains value:** Reporting and triage stay fast; security boundaries become explicit.

### 9) Per-task ephemeral identity

- Generate per-task short-lived secret tokens or handles.
- Agent receives only the secret handle; orchestrator resolves plaintext just-in-time.

**Retains value:** Agent remains autonomous in browser tasks; direct secret exposure is reduced.

### 10) Signed run manifests

- Create signed, append-only run summaries (status, timestamps, domain, reason).
- Keep secrets out of manifests.

**Retains value:** Better auditability without exposing credentials.

## Cross-Cutting Policy Recommendations

- **Default-safe mode:** all hardening features on by default, explicit flags to relax.
- **Data retention TTL:** auto-delete run artifacts after configurable days.
- **Post-run scrub command:** one command to remove logs, temp prompts, stale exports.
- **Operator confirmation for high-value domains:** optional checkpoint before final submit.

## What Not to Do (Would Harm Product Value)

- Replacing autonomous browsing with rigid per-site scripts.
- Requiring external cloud secret brokers for basic usage.
- Removing detailed failure reason tracking needed for triage.
- Enforcing so much interactivity that the pipeline becomes manual again.

## Suggested Implementation Order

1. Secret-minimized logging + prompt ephemerality.
2. Encrypted local secret storage with run unlock key.
3. Capability-based tool profiles.
4. Task-scoped sandboxing.
5. Two-database separation and run manifest signing.

This sequence gives the best risk reduction per engineering hour while preserving the system's core utility.

## Success Criteria

After hardening, the system should still:

- achieve comparable success rates on heterogeneous sites,
- support unattended batch execution,
- produce actionable triage metadata,
- and reduce recoverable plaintext artifacts on disk to near-zero by default.
