# Security Policy

codemcp-remote is a high-privilege local development tool. It can read and modify registered source repositories, execute explicitly registered development commands, and perform limited Bridge-owned Git recovery operations. Security reports are therefore treated as release-blocking when they affect project isolation, command restrictions, approvals, secrets, auditability, or mutation recovery.

## Supported versions

The project has not yet published its first stable release.

| Version | Security support |
|---|---|
| `main` / current pre-release | Best-effort during development; security fixes may be rebased into the next pre-release |
| `v0.1.x` | Planned: latest patch release only |
| `< v0.1.0` development snapshots | No long-term support guarantee |

A stable `v0.1.0` MUST NOT be published until the release gates in `docs/plans/v0.1.0/open-source-readiness-plan.md` pass.

## Reporting a vulnerability

Do not disclose exploitable details, credentials, tokens, private repository content, or proof-of-concept payloads in a public issue.

Preferred reporting order:

1. Use the repository's private GitHub vulnerability reporting / Security Advisory channel once it is enabled.
2. If private GitHub reporting is unavailable, contact the maintainer at `cloudw2023@gmail.com`.
3. If neither private channel is available, open a public issue containing only a request for a private security contact. Do not include vulnerability details.

Include, when possible:

- affected commit or version;
- operating environment;
- affected tool or security boundary;
- prerequisites;
- minimal reproduction steps;
- expected versus observed behavior;
- whether secrets, source data, Git state, or remote execution are exposed;
- whether exploitation requires a compromised local account, ChatGPT workspace, tunnel credential, or registered repository.

Never send real production credentials as part of a report. Revoke or rotate any credential that may already have been exposed.

## Security-relevant issue classes

Examples include:

- escaping a registered project root;
- bypassing symlink, junction, or reparse-point restrictions;
- reading denied secret paths;
- invoking an unregistered command or injecting additional arguments;
- bypassing approval or replaying an approval across operations, sessions, or projects;
- mutation replay caused by retry, reconnect, or idempotency failure;
- incorrect cross-session or cross-project operation access;
- rollback that overwrites an externally changed branch or HEAD;
- a side effect reported as failed when its outcome is actually unknown;
- exposure of runtime API keys, approval tokens, source contents, or sensitive diffs in logs/audit output;
- Bridge binding to a non-loopback interface contrary to policy;
- hidden model/provider egress from Bridge or its execution backend;
- dependency or packaging compromise that changes the trusted execution path.

Normal feature requests, unsupported-platform issues, documentation problems without security impact, and expected rejection by the policy engine should use the normal issue tracker.

## Disclosure principles

The project follows these principles:

- validate the report privately before public disclosure;
- prefer a minimal, reviewable fix that preserves fail-closed behavior;
- add a regression test whenever the issue can be reproduced safely;
- update `docs/architecture/security-model.md` or `docs/architecture/threat-model.md` when a trust assumption changes;
- do not publish exploit details before users have a reasonable upgrade path;
- identify affected versions and required credential rotation when relevant;
- never downgrade an uncertain mutation result merely to improve availability.

No fixed response-time SLA is promised for the initial open-source release. Critical issues affecting arbitrary command execution, project isolation, secret disclosure, approval bypass, or destructive Git behavior should block a release until resolved.

## Credential exposure

If a Tunnel control-plane key or another runtime secret may have leaked:

1. revoke or rotate it at the issuing service;
2. stop the Bridge and Tunnel while investigating;
3. remove the credential from local files and shell history where applicable;
4. scan the Git working tree and history before any public push;
5. inspect logs and release artifacts;
6. do not treat deletion from the latest commit as sufficient if the secret entered Git history.

## Security boundaries

The authoritative design description is `docs/architecture/security-model.md`. Threats and residual risks are tracked in `docs/architecture/threat-model.md`.

Important non-guarantees:

- a compromised local OS account is outside the protection boundary;
- a compromised dependency or execution backend may create risk that the Bridge can only detect partially;
- repository content may be malicious or prompt-injecting and must never be treated as authorization;
- Secure MCP Tunnel provides transport connectivity and does not replace Bridge project registration, approval, or audit policy;
- the initial release is single-user and does not provide independent multi-user identity or RBAC.
