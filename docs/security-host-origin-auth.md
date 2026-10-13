# Host, Origin and scoped bearer authentication

The gateway checks HTTP Host/Origin allowlists and authorizes each caller for declared capabilities and solution/project scope. Stdio uses a registered caller credential reference. See [connection setup](install-and-upgrade.md#connect-your-assistant).

## Why a loopback bind is not a security control

A server bound to `127.0.0.1` is still reachable from any page the user's own
browser loads, so "it only listens on localhost" answers a different question
than "may this request be served". The policy therefore validates `Host` and
`Origin` even when the bind address is loopback, and `SecurityPolicy` refuses to
be constructed with an empty or wildcarded allowlist so no permissive default
can appear by accident.

| Value | Rule |
| --- | --- |
| `Host` absent or malformed | refused (`FORBIDDEN`, 403) |
| `Host` entry without a port, e.g. `127.0.0.1` | admits any port on that host |
| `Host` entry with a port, e.g. `127.0.0.1:8765` | admits only that port |
| `Host` lookalike sharing only the port, e.g. `evil.example:8765` | refused |
| `Origin` absent | permitted, because non-browser clients do not send one |
| `Origin` present but not on the allowlist | refused (`FORBIDDEN`, 403) |
| `Origin` on another port, `null`, or a non-HTTP scheme | refused |
| any allowlist entry containing `*` | ignored at match time and rejected at construction |

`loopback_policy(port)` names the loopback interface and pins the port rather
than pattern-matching it, so it still rejects a lookalike host and a second port.

## Capability is not the same as holding a token

A bearer token is registered against a `ScopedToken`: an audience, a set of
capabilities and an explicit solution (and optionally project) scope.

- Capabilities use the canonical names `read`, `reconcile` and `checkpoint`.
  Only `graph_status`, `graph_query`, `graph_version`, `graph_reconcile`,
  `graph_job` and `graph_verify` require a capability, and the mapping is keyed
  by the canonical tool catalog rather than by a local invention.
- A tool call is classified from its JSON-RPC body. `tools/call` for
  `graph_reconcile`, `graph_job` or `graph_verify` is a mutation and requires
  the matching capability; an unknown tool name is treated as `read` so the
  catalog owner still answers `NOT_FOUND` instead of this layer widening access.
- A declared `solution_id` must be inside the token's solution scope and every
  declared `project_id`/`project_ids` entry must be inside its project scope, so
  a read token cannot reach another solution by naming it.

## Audience separation

`MCP_AUDIENCE` is `axiom-mcp`; the daemon control audience is
`axiom-graphd-control`. A `TokenRegistry` is bound to exactly one audience and
refuses to register a token minted for another, so presenting the graphd control
credential to the MCP endpoint fails as `UNAUTHENTICATED` (401). The gateway
re-authorizes the incoming capability instead of inheriting daemon authority.

## Credential handling

Configuration never carries a token value. `token_reference_from_config` accepts
either `{"kind": "env", "variable": "..."}` or `{"kind": "file", "path": "..."}`
and refuses a mapping that also carries a `token`, `value` or `secret` key.
`TokenRegistry` resolves the reference once and stores only a SHA-256 digest, so
the plaintext is not retained and no error message, log line or JSON body can
echo it back. A malformed `Authorization` scheme produces a fixed message that
is derived from the policy and not from the presented value.

## Enforcement point

`SecurityMiddleware` is a pure ASGI middleware, deliberately not
`BaseHTTPMiddleware`: that class buffers the whole response and would stall the
MCP endpoint's `text/event-stream` frames. The middleware reads `Host` and
`Origin` from the scope for every HTTP request, and for the protected MCP path
it buffers a bounded request body (default 262144 bytes) only to classify the
JSON-RPC call, then replays it to the SDK handler unchanged. An oversized body is
refused with `VALIDATION_ERROR` (400) before parsing. Operational probes such as
`/healthz` and `/readyz` need no token but still need an allowlisted `Host`.

`build_gateway(..., security=..., authenticator=...)` installs the middleware;
omitting the policy installs nothing, because a transport-level default would
widen a policy this module owns.

## Errors

Refusals use the canonical codes from control api v1:
`VALIDATION_ERROR` 400, `UNAUTHENTICATED` 401, `FORBIDDEN` 403. The envelope is
`{code, message, retryable, details, request_id}` and never carries raw source,
a token, a stack trace or an unregistered absolute path.


## Guarantees and limits

- Supported native x64 targets and runtime requirements are described in [runtime compatibility](runtime-compatibility.md). Host application integration is a separate check.
- Body buffering is bounded but not streamed for an oversized POST: the request
  is refused, not truncated and answered.
- Credential files are resolved without an ACL check; owner-only permission
  enforcement belongs to installation and diagnostics.
