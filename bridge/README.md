# codemcp-remote Bridge

[Simplified Chinese](README.zh-CN.md)

`codemcp-remote-bridge` is the local policy and execution boundary used by codemcp-remote. It exposes a constrained MCP surface for operating on explicitly registered local Git repositories while keeping the Bridge itself loopback-only.

The Bridge provides:

- explicit project registration and path containment;
- sensitive-path denial;
- registered-command execution instead of arbitrary shell/argv;
- structured operation state and idempotency;
- one-time approval flows for high-risk actions;
- Bridge-owned Git checkpoints, bounded diffs, and compare-and-swap restore;
- audit and restart-safe `unknown` side-effect handling;
- managed native worker lifecycle;
- Cloudflare network-trust and optional OAuth Resource Server boundaries.

The Bridge contains **no model provider and no agent loop**. ChatGPT remains the reasoning engine; repository content is untrusted data and cannot authorize a privileged operation.

## Worker baseline

The current Windows packaged baseline uses the **native Windows codemcp worker**. WSL2 remains an optional source-mode compatibility fallback.

macOS native packaging is an active validation track. The native `arm64` and `x86_64` candidate build gate has passed, but clean-host acceptance remains required before macOS is described as supported.

See:

- [`../docs/architecture/architecture.md`](../docs/architecture/architecture.md)
- [`../docs/architecture/security-model.md`](../docs/architecture/security-model.md)
- [`../docs/guides/codemcp-baseline.md`](../docs/guides/codemcp-baseline.md)
- [`../docs/acceptance/macos-v0.1.0-validation.md`](../docs/acceptance/macos-v0.1.0-validation.md)

## Local development

From the repository root:

```text
uv sync --project bridge
uv run --project bridge codemcp-bridge-server check
uv run --project bridge ruff check bridge/src bridge/tests tests/integration
uv run --project bridge pytest -q bridge/tests tests/integration
```

The packaged runtime contract and release checks are documented in the repository-level [`README.md`](../README.md) and [`docs/README.md`](../docs/README.md).
