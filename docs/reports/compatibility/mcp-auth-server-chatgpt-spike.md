# Phase 5.5.7 — mcp-auth-server + ChatGPT Live Interoperability Acceptance

Status: **IN PROGRESS — RFC 9728 repository fix PASS; managed reinstall harness READY; new installer candidate READY; live reinstall and ChatGPT proof pending**

Date: 2026-08-26

Repositories:

```text
codemcp-remote
  branch: codex/20260824
  repository-side acceptance code baseline before this record:
  f85010169fdd79a1ed0b298082a2015ff97d8e4c

mcp-auth-server
  version: 0.1.0
  current clean repository HEAD:
  b2372b61cf702874c6cae438ba504efe8bc0b4e6
  @cloudflare/workers-oauth-provider: 0.10.3
  live staging issuer:
  https://auth-staging.quickclip.cc

codemcp-remote public MCP resource:
  https://codemcp.quickclip.cc/mcp
```

The live staging issuer and Cloudflare MCP route are now assigned. The current `mcp-auth-server` repository is clean at the HEAD above, and its Phase 8 acceptance already records `https://auth-staging.quickclip.cc` as the live public issuer. The exact deployed Worker version/build corresponding to this acceptance still must be captured before final PASS; repository HEAD alone is not treated as proof of the deployed Worker version.

## Final architecture under acceptance

```text
ChatGPT
  |
  | OAuth Authorization Code + PKCE
  v
independent mcp-auth-server
  |
  | opaque access token
  v
https://<mcp-host>/mcp
  |
  | Cloudflare Tunnel only
  v
127.0.0.1:46200/mcp
  |
  | authenticated mcp-rs-verification-v1 online validation
  v
independent mcp-auth-server
```

Cloudflare Access identity assertions are not part of the authorization contract. The Bridge must accept or reject requests solely through its external OAuth Resource Server contract plus existing Bridge policy.

## Repository-side evidence

The Phase 5.5.7 clean Windows harness is now Cloudflare-first and preserves the OpenAI Tunnel only as an explicit compatibility option.

It proves or enforces before live ChatGPT testing:

- exact installer SHA verification;
- native local worker;
- isolated runtime without Python/uv/pwsh;
- Git available;
- bundled `cloudflared.exe`;
- loopback-only origin;
- Cloudflare tunnel token enters through process environment and is reloaded from Windows DPAPI;
- Resource Server verification secret enters through process environment and is reloaded from Windows DPAPI;
- canonical OAuth resource equals the public Cloudflare MCP URL;
- `mcp-rs-verification-v1`;
- no embedded `mcp-auth-server` runtime/private signing/user/client/refresh-token state;
- disposable `phase5-clean` repository with `README.md`, `PHASE5_ACCEPTANCE.txt`, and `pyproject.toml`, but no `codemcp.toml`;
- rerun ownership check for the fixed `phase5-clean` registration before creating a fresh disposable Git baseline;
- managed reinstall ownership check for the fixed AppId, install directory, app root, transport/resource configuration, and non-secret phase state;
- installed executable checksum manifest verification plus bundled `cloudflared.exe` presence;
- native PowerShell parser validity of the acceptance harness.

Current full regression after the harness rerun fix:

```text
223 passed
6 skipped
0 failed
```

Accepted installer from Phase 5.5.6:

```text
codemcp-remote-setup.exe
SHA-256:
7716e7bf7c5ceff536744f6342f1e7f6615eed770c7114c955a2bc70c33e6a93
```

## 2026-08-26 LIVE discovery blocker and repository fix

Clean Windows `Prepare` and `Start` passed with the accepted installer above, and the live
Authorization Server metadata returned the expected issuer, authorization endpoint, token endpoint,
Authorization Code/refresh-token grants, PKCE S256, and client metadata document support. The next
public discovery checks found two Resource Server surface blockers:

```text
GET https://codemcp.quickclip.cc/.well-known/oauth-protected-resource/mcp
-> 404 Not Found

GET https://codemcp.quickclip.cc/mcp
-> 401 Unauthorized
-> WWW-Authenticate: Bearer
```

The `401` fail-closed decision was correct, but the RFC 9728 Protected Resource Metadata endpoint was
absent and the Bearer challenge did not provide its `resource_metadata` URL. ChatGPT OAuth discovery
was therefore paused before OAuth E2E.

The repository fix is now **READY**:

- the configured canonical resource path derives the RFC 9728 metadata location, including nested
  resource paths;
- the unauthenticated metadata route returns the exact configured resource, configured Authorization
  Server issuer, and `header` bearer method;
- missing/inactive bearer responses retain `401` plus `Cache-Control: no-store` and advertise the
  same derived metadata URL;
- `/healthz` remains outside the OAuth gate;
- Resource Server id, verification secret, bearer token, Cloudflare identity headers, and loopback
  origin are not authorization inputs or public metadata;
- `mcp-rs-verification-v1` online validation behavior is unchanged.

Repository validation is `219 passed / 6 skipped / 0 failed`. This is not LIVE proof. Installer
SHA-256 `7716e7bf7c5ceff536744f6342f1e7f6615eed770c7114c955a2bc70c33e6a93` predates the fix and is
**STALE** for this acceptance. ChatGPT OAuth discovery remains blocked until the replacement
candidate below is installed through clean `Prepare`/`Start` and both public curl checks are repeated
successfully.

## 2026-08-26 Windows installer candidate refresh

Build baseline:

```text
branch: codex/20260824
repository fix commit: 91017a0402656a773737209321bd66634828f295
working tree before build: clean
```

The frozen Windows packaging workflow removed the previous installer work directory, ran PyInstaller
with `--clean`, rebuilt the onedir runtime from the baseline above, restaged both frozen transport
clients, and recompiled the Inno Setup installer. The existing installed copy at
`%LOCALAPPDATA%\Programs\codemcp-remote` was deliberately not upgraded or removed: the installer
workflow rejected its isolated install/upgrade/uninstall smoke because that copy is outside the
workflow-owned `.local\installer-smoke` directory. The candidate was therefore compiled with the
workflow's supported `-SkipSmoke` option after the clean-built payload was complete.

New candidate:

```text
installer:
.local\installer-dist\codemcp-remote-setup.exe

NEW CANDIDATE SHA-256:
ead65eceea08b97f13c46710b7301b086217712da896e3f71951356f1d9809ed

OLD STALE SHA-256:
7716e7bf7c5ceff536744f6342f1e7f6615eed770c7114c955a2bc70c33e6a93

release-candidate ZIP SHA-256:
0c31e53df630f4dd1ea588377e4030791fbbd3c29994f9e99fccb66e58379fbe
```

The packaged executable in the exact Inno Setup source payload returned `200 application/json` for
`GET /.well-known/oauth-protected-resource/mcp` with resource
`https://resource.example.com/mcp`, Authorization Server `https://auth.example.com`, and bearer method
`header`. A missing-bearer `GET /mcp` returned `401`, `Cache-Control: no-store`, and:

```text
WWW-Authenticate: Bearer resource_metadata="https://resource.example.com/.well-known/oauth-protected-resource/mcp"
```

Post-build regression is `41 passed`: 5 Windows installer tests, 6 Phase 5.5.7 clean Windows harness
tests, and 30 OAuth Resource Server tests. Payload checks reconfirmed pinned cloudflared `2026.7.3`,
the optional tunnel-client `0.0.12` compatibility payload, matching installer/checksum/release
manifests, and no bundled runtime state, DPAPI files, external auth-server state, or verification
secret.

| Phase 5.5.7 state | Result |
|---|---|
| Repository fix | PASS |
| Prior installer candidate (`ead65ece...`) | SUPERSEDED FOR RERUN |
| Live installed binary | PENDING REINSTALL |
| Live RFC 9728 verification | PENDING |
| ChatGPT OAuth | PENDING |

This candidate has not completed user clean Windows `Prepare`/`Start`, public curl verification, or
ChatGPT OAuth E2E. The LIVE blocker is not PASS.

## 2026-08-26 LIVE reinstall rerun blocker

The first live reinstall attempt with candidate SHA-256
`ead65eceea08b97f13c46710b7301b086217712da896e3f71951356f1d9809ed` stopped during `Prepare`:

```text
codemcp-remote.exe project add phase5-clean
-> project already exists: phase5-clean
```

The previous `Prepare` had left the `phase5-clean` registration in the persisted
`%LOCALAPPDATA%\codemcp-remote\config\projects.toml` even though the disposable filesystem project
had been recreated. `init` intentionally preserves that configuration and the product CLI had no
formal project unregister operation, so `project add` correctly failed instead of silently replacing
an unknown registration. This is a harness rerun blocker, not an OAuth or Cloudflare contract change.

The repository fix adds a formal `project remove <project-id> --expected-root <project-root>` operation.
`Prepare` now treats a missing registration as the first-run case, removes an existing registration
only when its resolved root exactly matches the harness-owned disposable root, and fails closed on any
other root. It then removes only the fixed acceptance project subtree, creates a new Git repository,
and records its new baseline. DPAPI tunnel/auth credentials and the rest of the runtime state are not
deleted. A custom `-ProjectRoot` is rejected; the harness manages only its fixed acceptance root.

The harness rerun fix is **READY**. The packaged CLI change required a replacement installer candidate
below; no user `Prepare`/`Start`, Cloudflare change, Cleanup, or ChatGPT OAuth action has been run after
this fix. Live reinstall remains pending.

## 2026-08-26 harness rerun fix installer candidate

The clean Windows packaging workflow rebuilt the executable and installer from the repository containing
the harness rerun fix. The prior `ead65ece...` candidate contains the RFC 9728 fix but not this packaged
CLI operation; it is superseded for the rerun acceptance.

```text
installer:
.local\installer-dist\codemcp-remote-setup.exe

NEW CANDIDATE SHA-256:
b0c99f7b8aa8a78076c7645f5ce073b118c66fa03b40a8870a8b542dd869a36e

release-candidate ZIP SHA-256:
0a63c7acb2a09bcc00d250ed2a7177499025d3d495dc7b38ffd97de5667c078f

PRIOR CANDIDATE SHA-256 (superseded for rerun):
ead65eceea08b97f13c46710b7301b086217712da896e3f71951356f1d9809ed

OLD STALE LIVE SHA-256:
7716e7bf7c5ceff536744f6342f1e7f6615eed770c7114c955a2bc70c33e6a93
```

The exact staged packaged executable passed local smoke for `project remove` (matching root succeeds,
missing registration is idempotent, and a different root fails closed), RFC 9728 metadata, and the
Bearer challenge. Targeted lifecycle/harness tests passed (`21 passed`), and the complete repository
regression passed (`223 passed / 6 skipped / 0 failed`). No Live endpoint was revalidated.

| Phase 5.5.7 state | Result |
|---|---|
| Repository fix | PASS |
| Harness rerun fix | READY |
| New installer candidate | READY |
| Live installed binary | PENDING REINSTALL |
| Live RFC 9728 verification | PENDING |
| ChatGPT OAuth | PENDING |

## 2026-08-26 managed reinstall harness fix

The second LIVE reinstall blocker occurred before project reset: `Prepare` rejected every existing
installation solely because the production Inno AppId was registered. That guard could not distinguish
the harness-owned `%LOCALAPPDATA%\Programs\codemcp-remote` installation from an unrelated user install.

The repository fix is now **READY** and remains limited to the clean Windows harness. `Prepare` now:

- allows first install when the AppId is absent;
- allows a controlled same-AppId upgrade only when the fixed install directory and
  `%LOCALAPPDATA%\codemcp-remote\phase5-validation.json` prove Phase 5.5.7 ownership (`phase`, fixed
  project ID/root, fixed app root, transport and canonical OAuth configuration);
- rejects missing, corrupt, mismatched, or incomplete state and any other install location before
  invoking the installer;
- stops the existing runtime through the formal packaged `stop` command, then invokes Inno Setup with
  `/NOSTOPLIFECYCLE` so no unmanaged process is targeted;
- verifies the installed executable against its packaged `SHA256SUMS.txt` and records
  `previous_installer_sha256`, `current_installer_sha256`, and executable identity in the non-secret
  phase state; DPAPI secrets and user runtime data are not removed.

This is a repository/harness fix only. The packaged runtime and Inno payload were not changed, so the
current candidate remains valid:

```text
repository HEAD: d1c88519b1ac257c730ef0e05f843b2f25823501
installer candidate: b0c99f7b8aa8a78076c7645f5ce073b118c66fa03b40a8870a8b542dd869a36e
refreshed release-candidate ZIP: 9053041a675fe68baf1b5a1145ade0612508dfc50bcf9e8b5e8e87cba8322c28
```

No user `Prepare`, `Start`, `Cleanup`, Cloudflare change, or ChatGPT OAuth action has been run after
this fix. Managed reinstall is **READY**; the live installed binary and public RFC 9728 checks remain
**PENDING**.

| Phase 5.5.7 state | Result |
|---|---|
| Repository fix | PASS |
| Managed reinstall harness | READY |
| New installer candidate | READY (payload unchanged) |
| Live installed binary | PENDING REINSTALL |
| Live RFC 9728 verification | PENDING |
| ChatGPT OAuth | PENDING |

## Client-policy correction from the historical spike

The older Cloudflare Access spike is not the final auth contract.

The current `mcp-auth-server` client trust profile used for final acceptance is:

```text
public client only
token_endpoint_auth_method = none
authorization_code
PKCE S256
CIMD enabled
static public clients supported
public DCR endpoint disabled
refresh-token rotation enabled
exact resource binding
```

Therefore final Phase 5.5.7 acceptance does not require successful public DCR. It must instead verify that ChatGPT interoperates with the actually frozen client strategy through CIMD or a pre-registered public client. If ChatGPT requires DCR and cannot operate without it, that is a live interoperability blocker and must not be hidden by weakening policy.

Meaningful custom OAuth scope-to-tool enforcement remains **NOT PROVEN** and is not part of the authorization claim.

## PASS / PENDING matrix

| Requirement | Result | Evidence / remaining work |
|---|---|---|
| Installer hash / packaging | NEW CANDIDATE READY | SHA-256 `b0c99f7b8aa8a78076c7645f5ce073b118c66fa03b40a8870a8b542dd869a36e`; live reinstall pending |
| Native local worker / isolated PATH | PASS | Clean harness + prior native packaging acceptance |
| Cloudflared bundled / fixed transport | PASS | Packaging and regression gates |
| Loopback-only Bridge origin | PASS | Provider + harness contract |
| Tunnel-token DPAPI boundary | PASS | Harness + provider regression |
| Resource Server secret DPAPI boundary | PASS | Harness + auth regression |
| No embedded auth-server private state | PASS | Harness fail-closed scan |
| `mcp-rs-verification-v1` consumer behavior | PASS | Resource Server regression suite |
| Wrong-resource / inactive / outage unit behavior | PASS | Resource Server security regression |
| Current full regression | PASS | 223 passed / 6 skipped / 0 failed after the harness rerun fix |
| Real public Cloudflare hostname/tunnel | ASSIGNED / LIVE PROOF PENDING | `https://codemcp.quickclip.cc/mcp` |
| Real deployed auth issuer | LIVE ISSUER ASSIGNED | `https://auth-staging.quickclip.cc`; positive OAuth still pending |
| codemcp Resource Registry entry | PASS | resource_id `0a8721b3-8944-47a5-b1ce-7351963fcb71`; resource `https://codemcp.quickclip.cc/mcp` |
| Clean Windows Prepare | MANAGED REINSTALL READY — LIVE PENDING | Existing harness-owned install is now upgradeable; install `b0c99f7b...` and rerun pending |
| Exact deployed auth-server commit/build | PARTIAL | Clean repo HEAD `b2372b61cf702874c6cae438ba504efe8bc0b4e6`; deployed Worker version still must be captured |
| ChatGPT OAuth discovery | BLOCKED — MANAGED REINSTALL READY / LIVE PENDING | Install `b0c99f7b...`, complete Prepare/Start, then repeat public RFC 9728 curl proof before OAuth E2E |
| CIMD/static-client interoperability | PENDING LIVE | Use current ChatGPT UI values |
| Authorization Code + PKCE | PENDING LIVE | Requires real identity/session |
| Refresh/session renewal | PENDING LIVE | Must preserve exact resource binding |
| Tool discovery | PENDING LIVE | ChatGPT connector |
| `project_open phase5-clean` | PENDING LIVE | ChatGPT connector |
| `development_ready == true` | PENDING LIVE | Disposable project |
| `file_read PHASE5_ACCEPTANCE.txt` | PENDING LIVE | Expected `phase5-clean-machine` |
| Remote mutation + checkpoint | PENDING LIVE | Disposable project only |
| Identical replay | PENDING LIVE | Must return original operation/checkpoint |
| Approval + checkpoint restore | PENDING LIVE | Canonical restore request hash |
| Final baseline HEAD + clean | PENDING LIVE | Must exactly match recorded baseline |
| Negative live credential without Git change | PENDING LIVE | Wrong-resource/invalid/revoked vector |
| Cloudflare identity headers unnecessary | PENDING LIVE | Authenticated MCP path must work without them |
| Cleanup/uninstall | PENDING LIVE | Run only after evidence capture |

## Live stop gate

Phase 5.5.7 must remain **IN PROGRESS** until all PENDING LIVE items above are evidenced.

Do not:

- substitute the `.invalid` staging issuer;
- enable development identity on a public domain;
- re-enable DCR just to make ChatGPT connect;
- put tunnel/auth credentials in source, docs, command-line arguments, or chat;
- use Cloudflare Access identity headers as the Bridge authorization truth;
- mark Phase 5.5 or `v0.1.0` packaging frozen before the complete remote mutation/replay/restore, negative-auth, refresh, and cleanup evidence exists.

After a real Cloudflare tunnel and real `mcp-auth-server` deployment are prepared, run the clean-machine `Prepare` and `Start` actions described in the two Phase 5.5.7 setup guides, then continue this record with the live ChatGPT evidence.
