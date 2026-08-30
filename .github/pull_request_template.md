## Summary

Describe the problem and the minimal change.

## Scope

- In scope:
- Explicitly out of scope:

## Validation

- [ ] Tests added or updated
- [ ] `ruff check` passes
- [ ] `ruff format --check` passes
- [ ] pytest passes for the affected scope
- [ ] `git diff --check` passes
- [ ] Documentation updated when behavior changed

## Security review

- [ ] No new arbitrary path, shell, argv, model-call, or network authority
- [ ] MCP schema/tool changes are documented and tested
- [ ] Sensitive-path, approval, idempotency, audit, Git, and recovery impacts reviewed
- [ ] `docs/security-model.md` / `docs/threat-model.md` updated if a trust boundary or mitigation changed
- [ ] No real credentials, logs, local databases, or private project data included

## Compatibility / recovery

Describe platform, upgrade, rollback, or unknown-side-effect implications.

## Release-gate evidence

Do not mark Phase 6/7 or release gates PASS unless they were actually executed. Link or summarize evidence when applicable.
