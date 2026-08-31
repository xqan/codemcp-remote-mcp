# codemcp-remote Documentation

[Simplified Chinese](zh-CN/README.md)

The English documentation is the **canonical project documentation**. Simplified Chinese documentation is maintained separately under [`docs/zh-CN/`](zh-CN/) as an adapted user-facing track. Historical validation evidence is not duplicated unless a translation is useful for operators.

## Start here

- [Project overview](../README.md)
- [Architecture baseline](architecture/architecture.md)
- [Windows build, install, and use](guides/windows-build-install-use.md)
- [Operations runbook](guides/operations-runbook.md)
- [Cloudflare Tunnel + ChatGPT network trust](guides/cloudflare-tunnel-setup.md)
- [Security model](architecture/security-model.md)
- [Threat model](architecture/threat-model.md)
- [Git checkpoint and rollback policy](architecture/git-policy.md)
- [macOS build, install, and clean-host validation](guides/macos-build-install-use.md)
- [macOS v0.1.0 validation ledger](acceptance/macos-v0.1.0-validation.md)

## Documentation model

The repository separates current instructions from plans and historical evidence so that an old acceptance record cannot accidentally override current product behavior.

| Path | Purpose | Authority |
| --- | --- | --- |
| `README.md` | Product overview and primary entry point | Current public overview |
| `docs/architecture/` | Current architecture and security boundaries | Normative |
| `docs/guides/` | Operator, deployment, build, and recovery instructions | Current operational guidance |
| `docs/acceptance/` | Active acceptance criteria and release gates | Current release criteria |
| `docs/implementation-plan.md` | Current active implementation plan | Planning only; not proof of support |
| `docs/plans/` | Versioned or completed implementation plans | Historical/planning context |
| `docs/releases/` | Version-specific phase records | Historical release evidence |
| `docs/reports/` | Migration, testing, and compatibility evidence | Historical evidence |
| `docs/development-state.md` | Recoverable engineering checkpoint for active parallel work | Branch-local development state |
| `docs/zh-CN/` | Independent Simplified Chinese user documentation | Translation/adaptation; English remains canonical |

## Current product boundaries

### Windows

The Windows `v0.1.0` release baseline has closed its product/runtime acceptance gates. The distributed release is intentionally **NotSigned**, so SmartScreen/reputation warnings remain an accepted limitation. GitHub-hosted CI limitations recorded by the release process remain an explicit accepted risk rather than a false PASS.

The installed Windows runtime uses:

```text
ChatGPT Connector
  -> OpenAI / ChatGPT Connector egress
  -> Cloudflare WAF/IP allowlist
  -> Cloudflare Tunnel
  -> loopback codemcp-remote Bridge
  -> native Windows codemcp worker
  -> registered local Git repository
```

Git for Windows is the explicit runtime prerequisite. Python, `uv`, PowerShell 7, and WSL2 are not required by the packaged runtime.

### macOS

macOS dual-architecture packaging is an active additive release track. The native GitHub-hosted candidate gate has passed for `arm64` and `x86_64`, but real clean-host acceptance is still required before macOS is described as supported.

Current candidate policy:

- `codemcp-remote-v0.1.0-macos-arm64.tar.gz`
- `codemcp-remote-v0.1.0-macos-intel64.tar.gz`
- ad-hoc code signing;
- no Developer ID certificate;
- no notarization;
- expected Gatekeeper quarantine friction until the user verifies the archive and explicitly releases quarantine.

See the [active macOS implementation plan](implementation-plan.md) and [validation ledger](acceptance/macos-v0.1.0-validation.md).

### Remote transport and identity

The recommended personal deployment is Cloudflare network trust with ChatGPT Connector `Authentication = No authentication`.

Cloudflare IP allowlisting is a **network provenance boundary**, not human/user/workspace/conversation authentication. The optional OAuth Resource Server profile remains available when subject/client/scope identity is required. OpenAI Secure MCP Tunnel remains an optional compatibility transport.

## Current architecture and security

Read these before exposing a repository remotely:

- [Architecture baseline](architecture/architecture.md)
- [Security model](architecture/security-model.md)
- [Threat model](architecture/threat-model.md)
- [Git policy](architecture/git-policy.md)
- [codemcp pinned baseline](guides/codemcp-baseline.md)

Important invariants include:

- only explicitly registered projects are accessible;
- all file paths remain inside the registered project root;
- sensitive paths are denied by default;
- arbitrary shell and arbitrary caller-supplied argv are not exposed;
- mutations are serialized, idempotent, checkpointed, and Git-guarded;
- high-risk operations use explicit approval;
- uncertain side effects remain `unknown` until reconciled;
- the Bridge remains loopback-only;
- repository content cannot grant itself privileges.

## Operator guides

- [Windows build, install, and use](guides/windows-build-install-use.md)
- [Clean Windows release validation](guides/clean-machine-validation.md)
- [Operations runbook](guides/operations-runbook.md)
- [Cloudflare Tunnel + ChatGPT network trust](guides/cloudflare-tunnel-setup.md)
- [OpenAI Secure MCP Tunnel compatibility setup](guides/tunnel-setup.md)
- [External mcp-auth-server setup](guides/external-mcp-auth-setup.md)
- [macOS build, install, and clean-host validation](guides/macos-build-install-use.md)

## Acceptance and release evidence

Current acceptance criteria:

- [Phase 6 Windows operational validation](acceptance/phase-6-validation.md)
- [Phase 7 / v0.1.0 final release gate](acceptance/acceptance-test-plan.md)
- [macOS v0.1.0 validation ledger](acceptance/macos-v0.1.0-validation.md)

Historical evidence:

- [v0.1.0 phase records](releases/v0.1.0/)
- [Migration reports](reports/migration/)
- [Testing reports](reports/testing/)
- [Compatibility reports](reports/compatibility/)

Historical evidence describes what was verified at a specific point in time. It must not be used as a replacement for current architecture, guides, or acceptance criteria.

## Plans

- [Active macOS dual-architecture implementation plan](implementation-plan.md)
- [Project registry hot-reload plan](plans/project-registry-hot-reload-plan.md)
- [v0.1.0 Cloudflare transport/OAuth plan](plans/v0.1.0/cloudflare-transport-oauth-plan.md)
- [v0.1.0 open-source readiness plan](plans/v0.1.0/open-source-readiness-plan.md)
- [Frozen Windows release baseline](plans/v0.1.0/windows-release-baseline-2026-08-28.md)

A plan authorizes or describes intended work only. It does not prove that the planned capability is implemented or supported.

## Language policy

1. English is the canonical/default language for `README.md`, `bridge/README.md`, and documentation outside `docs/zh-CN/`.
2. Simplified Chinese documentation lives only in dedicated `*.zh-CN.md` files and `docs/zh-CN/`.
3. Chinese documentation is adapted for usability rather than mechanically mirroring every historical file.
4. Normative behavior, release gates, and security claims are resolved from the English canonical document when translations differ.
5. A language-guard test prevents Han text from being reintroduced into default English documentation.

## Maintenance rules

1. Keep current architecture, guides, and acceptance documents distinct from historical reports.
2. Keep the active implementation plan at `docs/implementation-plan.md`; archive it under `docs/plans/` when the work is frozen or superseded.
3. Put migration, testing, and compatibility evidence under their matching `docs/reports/` subdirectories.
4. Update links whenever documents move.
5. Preserve `docs/development-state.md` as a recoverable checkpoint and record parallel branches explicitly.
6. Run the project regression suite after documentation moves that can affect packaging, tests, or published links.
