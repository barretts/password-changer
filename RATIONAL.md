# Security Rationale

This repository handles credentials in plaintext during execution. That is normally a bad pattern for production systems, but this project was built for a constrained, one-time migration workflow on a trusted local machine. This document explains why it was acceptable for this case, what controls were used, and why secrets are not committed to git.

## Context and Threat Model

- The goal was bulk password rotation across a large legacy vault export.
- The process runs locally, not as an internet-facing service.
- There is no multi-user server, no hosted API, and no shared database.
- The operator is the account owner rotating their own credentials.
- The objective was reducing manual exposure and fatigue while triaging many stale or dead accounts.

In short: this is an operator-run migration tool, not a long-lived credential platform.

## Why Plaintext Was Used Here

For this workflow, plaintext credentials were needed at runtime because the browser automation agent must:

1. Read the current password from imported vault data.
2. Log into each site.
3. Generate and submit a replacement password.
4. Save the new password for export to a destination password manager.

Encrypting credentials "at rest" inside this same local process would not remove the need to decrypt them immediately before browser use, and would have added complexity and failure modes that were out of scope for a short-lived migration pipeline.

## Why It Worked Well Enough for This Case

- It converted a large manual credential-rotation task into a manageable, auditable batch process.
- It reduced repetitive interactive handling of secrets across many sites.
- It produced structured outcomes (success, MFA blocked, stale password, dead site, etc.) for follow-up.
- It was intentionally scoped to a local, operator-controlled environment.
- It was intended for short-term use and cleanup, not continuous operation.

## How Secrets Are Kept Out of Git

Sensitive runtime artifacts are explicitly ignored in `.gitignore`:

- `passwords.db`
- `passwords.db-shm`
- `passwords.db-wal`
- `*.csv` (vault exports and generated CSV outputs)
- `tasks.txt`
- `logs/`, `.logs/`, `*.log`
- `.last_log_dir`, `.resume_tasks.txt`, `.active_tasks.txt`
- `/tmp/`

These patterns prevent local credential data, run logs, and transient task metadata from being tracked or committed under normal git workflows.

## Operational Guardrails in the Repo

- The automation prompt explicitly forbids destructive file operations by the agent.
- The workflow is designed to run locally with local tooling (including optional local vision model).
- Sensitive artifacts are generated into ignored paths.

## Residual Risk (Acknowledged)

This does not make the workflow "secure by default" for all contexts. If the local machine is compromised (malware, remote access, disk exfiltration, shell history leakage), plaintext secrets can still be exposed. This approach is acceptable only under the original assumptions:

- trusted local machine,
- single operator,
- temporary migration use,
- prompt post-run cleanup.

## Recommended Hygiene

- Run only on a personally controlled workstation.
- Avoid screen sharing or recording while running.
- Keep full-disk encryption enabled.
- Remove local CSV/DB/log artifacts after migration is complete.
- Rotate high-value accounts first and verify MFA is enabled afterward.

---

This is a pragmatic migration tool with explicit tradeoffs, not a general-purpose credential management system.
