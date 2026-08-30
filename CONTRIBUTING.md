# Contributing to codemcp-remote

Thanks for helping improve codemcp-remote. This project exposes controlled local code operations through MCP, so changes that expand execution authority receive extra scrutiny.

## Development setup

Requirements:

- Python 3.12+
- Git
- uv
- PowerShell 7 for Windows lifecycle scripts
- Windows 11 + WSL2 Ubuntu for the supported mutation deployment path

Install the Python environment:

```text
uv sync --project bridge --frozen --all-groups
```

Run the core checks:

```text
uv run --project bridge --frozen ruff check bridge/src bridge/tests tests/integration
uv run --project bridge --frozen ruff format --check bridge/src bridge/tests tests/integration
uv run --project bridge --frozen pytest -q bridge/tests tests/integration
uv run --project bridge --frozen codemcp-bridge-server check --bridge-config config/bridge.example.toml --projects-config config/projects.example.toml
git diff --check
```

The real Windows 11 + WSL2 + Secure MCP Tunnel release gates are documented in `docs/acceptance/phase-6-validation.md` and `docs/acceptance/acceptance-test-plan.md`.

## Pull requests

Keep each pull request focused. Include:

- the problem being solved;
- the intended behavior and non-goals;
- tests added or changed;
- security impact;
- compatibility impact;
- rollback or recovery considerations when mutation behavior changes.

Do not claim a release gate is PASS unless the documented command or manual procedure was actually executed and its evidence is available.

## Security-sensitive changes

Changes to any of the following require corresponding tests and documentation review:

- exposed MCP tools or schemas;
- project/path authorization;
- sensitive-path filtering;
- command registration or approval defaults;
- idempotency or operation state transitions;
- Git checkpoint/restore behavior;
- worker lifecycle, process boundaries, or environment forwarding;
- Tunnel configuration or transport assumptions;
- logging or audit data.

Update `docs/architecture/security-model.md` and `docs/architecture/threat-model.md` whenever a trust boundary, capability, mitigation, or residual risk changes.

## Design boundaries

The current project intentionally does not expose:

- arbitrary filesystem paths;
- arbitrary shell or caller-supplied argv;
- model-provider calls inside the Bridge;
- automatic push, merge, rebase, deploy, or branch deletion;
- native Windows Git-backed mutation as a supported path;
- multi-user identity or RBAC.

Proposals can discuss these areas, but implementations must not silently weaken the current fail-closed defaults.

## Secrets and test data

Never commit real API keys, Tunnel credentials, private keys, production project paths, local databases, logs, or `.env` files. Use synthetic fixtures and `example.invalid` addresses.

Security vulnerabilities should follow `SECURITY.md`, not public issue discussion.

## License

By contributing, you agree that your contribution is licensed under the repository's GNU Affero General Public License v3.0 only (`AGPL-3.0-only`).
