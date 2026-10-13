# MCP tools and response reference

The public stdio and Streamable HTTP entrypoints expose six tools. This reference describes their arguments, capabilities, response shapes and limits. Start with [installation and connection setup](../install-and-upgrade.md).

## Transports

| Surface | Channel | Contract this revision relies on |
| --- | --- | --- |
| Streamable HTTP | `POST /mcp` | The official SDK transport mounted at an exact path with no redirect; a real `initialize` handshake, event stream and session id. See [http-transport.md](../http-transport.md). |
| stdio | process stdin/stdout | Protocol frames on stdout only; every diagnostic on stderr with no banner. See [stdio-transport.md](../stdio-transport.md). |
| Health | `GET /healthz` | Minimal process health. It makes **no** readiness claim. |
| Readiness | `GET /readyz` | Query and control availability, reported separately; `503` with `NOT_READY` while a plane is down. |

A plain REST route that returns a hand-written `initialize`-shaped body is not a transport.
The two supported surfaces carry the same query core, so an HTTP client and a stdio client
must receive equivalent results for the same request.
[query-transports.md](../query-transports.md) records the JSON-first read model, the locked
launch modes that select a transport, and the reader failure states the two surfaces keep
apart.

## Tool catalog

Tool discovery exposes the names below. Each request is authenticated and authorized for its required capability and selected scope.

| Tool | Purpose | Capability | Side effect |
| --- | --- | --- | --- |
| `graph_status` | Solution/project freshness, coverage, generations, capabilities | `read` | none |
| `graph_query` | Graph operation: `search`, `context`, `neighbors`, `callers`, `dependencies`, `impact`, `path`, `changes` | `read` | none |
| `graph_reconcile` | Enqueue a graphd reconcile job for a scope | `reconcile` | Enqueue graphd job |
| `graph_job` | Job status or explicit cancel | `reconcile` | Status read; `cancel` is an explicit write |
| `graph_verify` | Bounded verification request against an expected fingerprint or barrier | `checkpoint` | Bounded verification request |
| `graph_version` | Selected components and their compatibility | `read` | none |

`graph_reconcile`, `graph_job` and `graph_verify` are the mutation paths. They reach beyond
the read-only plane and the gateway re-authorizes the incoming capability instead of
inheriting admin authority. Update, bootstrap and install are deliberately **not** MCP
tools: they travel through the CLI or the skill workflow, so no MCP tool downloads or
executes an update by default.

## `graph_reconcile` request and answer

`graph_reconcile` submits work to the graph daemon. Requests use `solution_id`, `scope`, optional project selectors, `wait_timeout_ms` and a required audit `reason`. Unexpected fields are refused with `VALIDATION_ERROR`.

| Field | Rule |
| --- | --- |
| `solution_id` | Required identifier. The token must carry `reconcile`; an invisible solution is `NOT_FOUND`. |
| `scope` | `dirty` (default), `project` or `full`. `project` requires an explicit project selector; `full` is explicit owner-authorized work. |
| `project_id` / `project_ids` | Only with `scope=project`. Validated against the registry and the token's project scope, so an unknown project is `NOT_FOUND`. |
| `reason` | Required, non-empty, at most 200 characters. It travels to the daemon's journal and is never echoed back. |
| `wait_timeout_ms` | `0`-30000, default `0`. Long parsing never holds the request open by default. |

The tool does not read a snapshot, parse a source file or write a shard: it authorizes, hands the
request to the `ControlPlane` protocol, and projects the daemon's answer through a closed
allowlist. A `202`-style answer is reported as `job` with exactly `{job_id, state,
target_event_seq, retry_after_ms}`; the `state` must be one of the `job schema.json` enum values,
so a non-conformant daemon is `DAEMON_UNAVAILABLE` rather than being echoed through. A
`200`-style answer (an existing verified publication already satisfies the barrier) is reported
as `publication` with `{catalog_generation_id, verification, dirty}`. An unreachable or unusable
control plane is `DAEMON_UNAVAILABLE` with `retryable: true`.

```json
{
  "schema_version": 1,
  "solution_id": "alpha",
  "scope": "project",
  "project_ids": ["auth-api"],
  "job": {"job_id": "job-0007", "state": "PENDING", "target_event_seq": 12, "retry_after_ms": 250},
  "publication": null,
  "warnings": []
}
```

## `graph_job` request and answer

``graph_job`` takes `job_id`, an optional `action` of `status` (default) or `cancel`, and an
optional `wait_ms` bounded to `0..30000`. The request is closed; `job_id` must match
`^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}`, so a path-shaped id never reaches the daemon.

Three properties are enforced here rather than delegated:

- **The handler makes exactly one control-plane call.** It never loops and never sleeps, so a
  client cannot make the gateway spin on a queue; the daemon owns waiting. A test counts the
  recorded calls.
- **Job ownership is verified on the answer.** The job's `solution_id` must be visible to the
  token and registered locally; otherwise the answer is `NOT_FOUND`, the same answer an unknown
  job gets, so job ids are not an enumeration oracle.
- **Cancel is capability-checked before the daemon.** A caller without `reconcile` gets
  `FORBIDDEN` and no control-plane call is made. A `status` read cannot be refused that early -
  the daemon is the only party that knows who owns the job - so ownership for a read is enforced
  on the answer instead. `wait_ms` with `action=cancel` is a `VALIDATION_ERROR`: a cancel is
  idempotent and does not wait.

The answer is `{schema_version, action, solution_id, job, warnings}`. The `job` object carries the
`job schema.json` fields plus `retry_after_ms`, a `terminal` boolean derived from the state, and
`progress` restricted to the documented progress units (`units_done`, `units_total`,
`projects_done`, `projects_total`, `files_scanned`, `shards_written`). A percentage field is
dropped and named in `warnings` (`percentage_not_carried:<key>`), because
control api v1 says not to invent percentages. A state outside the schema enum is
`DAEMON_UNAVAILABLE` rather than echoed through.

```json
{
  "schema_version": 1,
  "action": "status",
  "solution_id": "alpha",
  "job": {
    "job_id": "job-0001",
    "kind": "ParseBatch",
    "state": "RUNNING",
    "solution_id": "alpha",
    "attempt": 0,
    "fence": 3,
    "target_event_seq": 41,
    "terminal": false,
    "progress": {"units_done": 3, "shards_written": 2}
  },
  "warnings": []
}
```

## `graph_verify` request and answer

``graph_verify`` takes `solution_id`, an optional project scope, and at least one of
`expected_fingerprint` (64 hexadecimal characters) or `target_event_seq` (a non-negative,
bounded barrier). A request with neither is a `VALIDATION_ERROR`. The token must carry
`checkpoint`; an invisible solution is `NOT_FOUND` and the control plane is consulted only after
that check.

**The four verification modes are reported separately, and an archive cannot claim current
filesystem state.** The answer always carries its `basis` - `archive` or `working_tree` - and a
`verification` object with one entry per dimension (`hash`, `schema`, `catalog`, `source`). A
dimension the daemon did not report is `{"mode": "not_run"}` with a
`dimension_not_reported:<name>` warning, and an unrecognised mode becomes
`{"mode": "unknown"}` with an `unrecognised_mode:<name>` warning: absence and novelty are never
promoted to a pass. A basis of `archive` whose `source` dimension claims `current` is refused
outright as `DAEMON_UNAVAILABLE` with `{dimension: "source", basis: "archive"}`, because an
archived checkpoint cannot speak for the live working tree.

An asynchronous answer (`job_id` present) returns the job handle and `verification: null`, with a
`verification_incomplete` warning if the daemon also volunteered unmapped verification fields.

```json
{
  "schema_version": 1,
  "solution_id": "alpha",
  "project_ids": ["auth-api"],
  "basis": "archive",
  "verification": {
    "hash": {"mode": "recomputed", "matched": true, "observed": "3f0a...6e7f"},
    "schema": {"mode": "declared"},
    "catalog": {"mode": "matched"},
    "source": {"mode": "archive_contents"}
  },
  "job": null,
  "warnings": []
}
```

## `graph_version` request and answer

``graph_version`` reports the selected components, their compatibility and update availability,
and it is deliberately the one tool that can never act. The request is closed and carries only
`components` (a subset of `axiom-mcp`, `axiom-graphd`, `axiom-specs` and `axiom-skills`, defaulting
to the whole set) and `check_update` (a boolean, default `false`). An unregistered component name
is a `VALIDATION_ERROR` naming the `allowed` set, and the token must carry `read`.

A component this process cannot observe is reported as unobserved rather than guessed. Only
`axiom-mcp` is observable from inside itself, so it carries the pinned `version`, `spec_version`,
`spec_revision`, a `compatible` flag and the pin checker's `reasons` verbatim. `axiom-graphd` is
`observed` only when a control client is wired into this process, and `axiom-specs` and
`axiom-skills` report `not_observable_from_this_component`. `compatibility` restates the runtime
and SDK pins (`python`, `supported`, `pinned_python`, `pinned_sdk`, `installed`,
`protocol_versions`, `protocol_minimum`) plus the `reasons` that make `compatible` false; an
incompatible runtime also raises a `runtime_pin_mismatch` warning.

`update` is `not_checked` until the caller asks for it, and `check_update: true` runs the
**read-only** `axiom_mcp.update.check_update`, whose `status`, `available`, `compatible`,
`channel`, `source_origin`, `needs_restart` and `reasons` are relayed verbatim. Nothing on this
surface installs; the answer always states the boundary explicitly:

```json
"installation": {
  "implicit_install": false,
  "reason": "no_install_path_on_the_mcp_surface",
  "applies_via": "cli_or_skill_workflow",
  "commands": {"short_command": "axm", "long_form": "axiom-cli", "update": "axm update", "doctor": "axm doctor", "version": "axm version", "uninstall": "axm uninstall", "update_one_liner": {"windows": "…update.ps1 | iex", "posix": "…update.sh | sh"}, "install_one_liner": {"…": "…"}, "note": "…"}
}
```

Update, bootstrap and install stay on the CLI/skill workflow. A regression test replaces the
update applier with a tripwire that fails if this tool reaches it, so AC1 is enforced rather
than promised.

```json
{
  "schema_version": 1,
  "components": [
    {
      "component": "axiom-mcp",
      "observed": true,
      "version": "0.1.6",
      "spec_version": "2.0.0-draft.1",
      "spec_revision": "80f44e8",
      "compatible": true,
      "reasons": []
    }
  ],
  "dimensions": {
    "component": "axiom-mcp",
    "version": "0.1.6",
    "spec_version": "2.0.0-draft.1",
    "graph_schema": 1,
    "control_api": 1,
    "queue_schema": 1,
    "build_revision": "80f44e836ced442e8f3ea33d167bd369ed6796bc",
    "update_status": "not_checked"
  },
  "compatibility": {"compatible": true, "runtime": {}, "sdk": {}, "reasons": []},
  "update": {
    "status": "not_checked",
    "installed": "0.1.6",
    "available": null,
    "compatible": null,
    "needs_restart": false,
    "reasons": ["update_check_not_requested"]
  },
  "installation": {
    "implicit_install": false,
    "reason": "no_install_path_on_the_mcp_surface",
    "applies_via": "cli_or_skill_workflow",
  "commands": {"short_command": "axm", "long_form": "axiom-cli", "update": "axm update", "doctor": "axm doctor", "version": "axm version", "uninstall": "axm uninstall", "update_one_liner": {"windows": "…update.ps1 | iex", "posix": "…update.sh | sh"}, "install_one_liner": {"…": "…"}, "note": "…"}
  },
  "warnings": []
}
```

## `graph_query` request

Requests use a closed schema:
`additionalProperties` is `false`, so an extra key is a `VALIDATION_ERROR` rather than a
silently ignored input.

| Field | Type | Rule |
| --- | --- | --- |
| `solution_id` | string | Required. `^[a-z][a-z0-9-]{0,62}$`. |
| `operation` | enum | Required. One of `search`, `context`, `neighbors`, `callers`, `dependencies`, `impact`, `path`, `changes`. |
| `project_id` | string | One project. Must **not** be combined with `project_ids`. |
| `project_ids` | array | 1-64 unique ids matching the id pattern. Must not be combined with `project_id`. Omitted scope means the registered solution within the caller's authorization. |
| `target` | string | Required for `context`, `neighbors`, `callers`, `dependencies`, `impact` and `path`. |
| `target_to` | string | Required for `path`. |
| `query` | string | Required for `search`. |
| `depth` | integer | `0`-`8`, default `2`. |
| `max_nodes` | integer | `1`-`500`, default `50`. |
| `max_edges` | integer | `0`-`1000`, default `100`. |
| `max_bytes` | integer | `512`-`32768`, default `8192`, metadata included. |
| `consistency` | enum | `allow_stale`, `require_fresh` or `pinned`, default `allow_stale`. |
| `catalog_generation_id` | string | `^[0-9a-f]{64}$`. Required when `consistency` is `pinned`. |
| `baseline_catalog_generation_id` | string | `^[0-9a-f]{64}$`. Baseline selector for `changes`; unavailable or unpinnable baselines are reported explicitly. |
| `cursor` | string | Opaque continuation token. |
| `edge_kinds` | array | Unique subset of the fifteen edge kinds below. |
| `direction` | enum | `incoming`, `outgoing` or `both`, default `both`. |
| `projection` | array | Unique subset of `identity`, `relations`, `source_locations`, `coverage`. |

The complete `edge_kinds` allowlist, in schema order: `CONTAINS`, `IMPORTS`,
`REFERENCES`, `CALLS`, `INHERITS`, `IMPLEMENTS`, `EXPOSES`, `CALLS_ENDPOINT`, `READS`,
`WRITES`, `EXECUTES_PROCEDURE`, `DEPENDS_ON`, `PUBLISHES`, `SUBSCRIBES`, `TESTS`.

`max_bytes` is a hard cap that includes metadata. When the result would exceed it, the
gateway truncates to fit and sets `truncated`; it does not exceed the cap to answer in
full. Traversal uses a visited set and hard expansion and wall-time budgets. There is no
arbitrary SQL, Cypher, code evaluation or unbounded regular expression anywhere in the
query path. That is enforced by the request parser (`tools/context.closed_arguments`)
rather than promised: `sql`, `cypher`, `command` and `script` are not fields of the request,
a request carrying one is a `VALIDATION_ERROR` that names only the key, and a selector that
cannot apply to the requested operation (`target` on `search`, `direction` on `changes`) is
refused rather than silently ignored.

### Budgets are ceilings, not hints

`depth`, `max_nodes`, `max_edges` and `max_bytes` are validated against the schema before
the query runs. A value above its maximum is refused with `LIMIT_EXCEEDED`; the gateway does not
clamp an over-budget request to the maximum and continue, because that would answer a
different question than the one asked. A budget that a caller leaves unset takes the
documented default.

## `graph_query` response

A successful query uses the response schema. A successful response carries:
`schema_version`, `solution_id`, `catalog_generation_id`, `project_generations`,
`freshness`, `coverage`, `verification`, `nodes`, `edges`, `truncated`, `warnings`, and
optionally `next_cursor`.

| Field | Shape |
| --- | --- |
| `schema_version` | const `1`. |
| `project_generations` | Array of `{project_id, generation_id}`, both ids validated by pattern. |
| `freshness` | One of `fresh`, `stale`, `updating`, `unknown`, `invalid`. |
| `coverage` | One of `complete_for_profile`, `partial`, `unsupported`. |
| `verification` | Object with required `mode` in `inventory_hash`, `watcher_hint`, `none`; optional `verified_at` and `source_fingerprint`. |
| `nodes` | Array of node objects; each requires `id`, `project_id`, `kind`, `name`, `qualified_name`, `language`, `source`, `identity_quality`, `attributes`, with `additionalProperties` false. |
| `edges` | Array of edge objects; each requires `id`, `source_id`, `target_project_id`, `kind`, `resolution`, `evidence`, `analyzer_id`, and either `target_id` or `unresolved_target` exactly once. |
| `truncated` | Boolean. |
| `warnings` | Array of non-empty strings. |

A failed query never carries a partial result envelope. The response is either a complete
result or the `error` variant:

```json
{
  "schema_version": 1,
  "solution_id": "alpha",
  "error": {
    "code": "SNAPSHOT_UNAVAILABLE",
    "message": "no readable generation is currently published",
    "retryable": true
  }
}
```

Provenance is carried in each node's `source` location and each edge's `evidence` and
`analyzer_id`. The schema deliberately has no competing `result` or `snapshot` envelope: a
consumer that finds one is reading a non-conformant server, not an alternate shape.

### Consistency and freshness

`allow_stale` (the default) answers from the pinned generation. A named
`catalog_generation_id` is always honoured as a pin: when the lane no longer publishes that
vector the answer is `SNAPSHOT_EXPIRED` rather than a read of whatever is current, and a
`pinned` request without the field is a `VALIDATION_ERROR`. `require_fresh` needs the
`reconcile` capability - a read-only token gets `FORBIDDEN` before the control plane is
consulted at all - and with the capability the request enqueues a bounded reconcile and
answers `NOT_READY` with the job id, because the request itself cannot prove the reconcile
finished. Every `graph_query` answer is `freshness=unknown` with `verification.mode=none`: a
pinned generation proves which bytes answered, not that the source tree was re-read.

`changes` compares the head against a baseline generation. The baseline vector cannot be
pinned through the read session at this revision, so a named baseline that is *not* the
current one answers `missing_baseline` with a `baseline_not_pinnable` warning instead of a
diff against whatever is current, while a baseline equal to the head is a comparable (empty)
diff. Facts are head-side only: an `added` fact is a head document and a `modified` fact is
rebuilt as one from the head side of the before/after pair, so every node still carries its
own `source`; `removed` facts belong to the baseline generation and are not carried.

### Freshness, coverage and verification are three different claims

They are separate fields on purpose, because collapsing them is how a caller treats a
stale-but-valid answer as fresh.

- `freshness` describes the relationship between the pinned generation and the live source
  inventory: `fresh`, `stale`, `updating`, `unknown` or `invalid`.
- `coverage` describes how much of the requested profile the generation actually answers:
  `complete_for_profile`, `partial` or `unsupported`. A partial solution reports `partial`
  and a partial result; it never fabricates an empty graph for a missing project.
- `verification.mode` names the basis for the freshness claim: `inventory_hash`
  (content-addressed), `watcher_hint` (an advisory hint, not a proof) or `none`.

An immutable historical generation does **not** imply live source freshness. A pinned query
answers from exactly that generation and reports its freshness honestly.

## Control client

`src/axiom_mcp/control_client.py` implements the `ControlPlane` protocol over the daemon's
control API. The audience boundary is enforced before a socket is opened: the client accepts only
a credential minted for `axiom-graphd-control`, so an MCP token can never be replayed to the
daemon and a control credential can never answer an MCP call.

| Operation | Request | Notes |
| --- | --- | --- |
| `status` | `GET /v1/solutions/{id}/status` | versions, freshness, coverage, queue counts |
| `reconcile` | `POST /v1/solutions/{id}/reconcile` | closed body: `scope`, `project_ids`, `reason`, `wait_timeout_ms` |
| `job` (`status`) | `GET /v1/jobs/{id}?wait_ms=` | reads one job; `wait_ms` is a bound, not a loop |
| `job` (`cancel`) | `POST /v1/jobs/{id}/cancel` | idempotent; carries no `wait_ms` |
| `verify` | `POST /v1/solutions/{id}/verify` | follows the reconcile envelope |

A solution or job id must match a bounded identifier pattern, so a path-shaped id never reaches
the wire. Retries are bounded and cannot be widened by configuration: only `429`, `503` and
transport failures are retried, `max_attempts` is capped at `MAX_ATTEMPTS_LIMIT` (5), the backoff
doubles from `0.25` s to a `2` s cap, and a `Retry-After` header is honoured but clamped to that
cap. Any other 4xx is raised on the first response, so a malformed request is never amplified into
a retry storm. A canonical code in the daemon's `{code,message,retryable,details,request_id}` body
wins; otherwise the HTTP status is mapped to the canonical code.

A transport failure becomes a retryable `DAEMON_UNAVAILABLE`, and that is what makes degraded
reader service real: `graph_reconcile`, `graph_job` and `graph_verify` report the outage, while a
`graph_query` under the default `allow_stale` consistency still answers from the pinned generation
with `freshness: unknown`.

## Capability and authorization

The gateway authorizes before it resolves a path. Every call carries a bearer token with a
declared audience and capability set:

| Term | Value |
| --- | --- |
| MCP audience | `axiom-mcp` |
| graphd control audience | `axiom-graphd-control` |
| Capabilities | `read`, `reconcile`, `checkpoint` |
| Mutation capabilities | `reconcile`, `checkpoint` |

Three statements are kept apart rather than collapsed:

- binding to loopback is **not** authentication, so `Host` and `Origin` are validated even
  on a `127.0.0.1` bind;
- holding a token is **not** holding a capability, so `graph_query` with a read token is
  allowed and `graph_reconcile` with the same token is refused with `FORBIDDEN`;
- the graphd control audience is **not** the MCP audience, so presenting the daemon control
  credential to `/mcp` is refused instead of being used to elevate the caller.

A token is scoped to solutions and projects. A call whose `solution_id` or `project_id` is
outside the token's scope is refused with `FORBIDDEN` - the gateway does not fall back to a
broader scope, and it does not reveal whether the out-of-scope solution exists. See
[security-host-origin-auth.md](../security-host-origin-auth.md) for the full policy.

## Error surfaces

MCP has a protocol layer and a tool layer, and a failure belongs on exactly one of them.
The surface is decided by the canonical code, not by the call site.

| Surface | When | Rendered as |
| --- | --- | --- |
| `protocol` | The request never became a meaningful call: malformed input, an incompatible input, missing or insufficient authentication, an unsupported operation. | A JSON-RPC error object. The JSON-RPC code stays generic; the canonical Axiom code travels in `error.data.code`. |
| `tool_result` | The call ran and failed for a domain reason: the snapshot expired, the project is missing, the daemon is unavailable. | A tool result with `isError: true`, one human-readable `content` entry, and `structuredContent.error`. |

Protocol-surface codes: `VALIDATION_ERROR`, `INCOMPATIBLE_INPUT`, `UNAUTHENTICATED`,
`FORBIDDEN`, `UNSUPPORTED_OPERATION`. Every other canonical code is a tool-result code.

### The error envelope

Both surfaces carry the same body,
`{code, message, retryable, details, request_id}`, where `request_id` is present when the
error carries one. `retryable` is not hand-set per call site: it comes from the canonical
retryable set `RATE_LIMITED`, `NOT_READY`, `DAEMON_UNAVAILABLE`, `PROJECT_UNAVAILABLE`,
`SNAPSHOT_UNAVAILABLE`, so a host can decide whether to retry without parsing prose.

A protocol-surface failure renders as a JSON-RPC error whose `data` is the envelope above:

```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "error": {
    "code": -32602,
    "message": "max_nodes must be at most 500",
    "data": {
      "code": "VALIDATION_ERROR",
      "message": "max_nodes must be at most 500",
      "retryable": false,
      "details": {}
    }
  }
}
```

A tool-result failure renders as a tool result rather than a transport fault:

```json
{
  "content": [{ "type": "text", "text": "SNAPSHOT_UNAVAILABLE: no readable generation is currently published" }],
  "structuredContent": {
    "error": {
      "code": "SNAPSHOT_UNAVAILABLE",
      "message": "no readable generation is currently published",
      "retryable": true,
      "details": {}
    }
  },
  "isError": true
}
```

### Canonical codes

Sixteen codes are accepted; a code outside this set raises rather than rendering, because a
free-form code is how two components start disagreeing about what happened.

| Code | Default surface | Retryable | HTTP |
| --- | --- | --- | --- |
| `VALIDATION_ERROR` | protocol | no | 400 |
| `UNAUTHENTICATED` | protocol | no | 401 |
| `FORBIDDEN` | protocol | no | 403 |
| `UNSUPPORTED_OPERATION` | protocol | no | 500 (unnamed by the control contract) |
| `INCOMPATIBLE_INPUT` | protocol | no | 422 |
| `NOT_FOUND` | tool_result | no | 404 |
| `CONFLICT` | tool_result | no | 409 |
| `LIMIT_EXCEEDED` | tool_result | no | 500 (unnamed) |
| `RATE_LIMITED` | tool_result | yes | 429 |
| `NOT_READY` | tool_result | yes | 503 |
| `DAEMON_UNAVAILABLE` | tool_result | yes | 503 |
| `PROJECT_UNAVAILABLE` | tool_result | yes | 503 |
| `SNAPSHOT_UNAVAILABLE` | tool_result | yes | 503 |
| `SNAPSHOT_EXPIRED` | tool_result | no | 410 |
| `SNAPSHOT_CORRUPT` | tool_result | no | 500 (unnamed) |
| `INTERNAL_ERROR` | tool_result | no | 500 |

HTTP status follows control api v1 for the codes that contract names. A code
the contract does not name keeps its canonical surface and reports `500` rather than
acquiring an invented status.

The JSON-RPC code a protocol error maps to:
`VALIDATION_ERROR` and `INCOMPATIBLE_INPUT` map to `-32602`; `UNSUPPORTED_OPERATION` and
`NOT_FOUND` map to `-32601`; `UNAUTHENTICATED` and `FORBIDDEN` map to `-32600`;
`INTERNAL_ERROR` maps to `-32603`; the parse and invalid-request codes `-32700` and
`-32600` complete the standard JSON-RPC set.

### Redaction and bounds

Nothing prohibited reaches either surface. `AxiomError` redacts its message and detail tree
**on construction**, following redaction policy: source secrets and absolute
local paths are replaced with `<redacted:{category}>`, an absolute path inside the
configured repository root becomes a repository-relative path, and the detail tree is
bounded to depth `6`, `64` items and `2048` characters per string. Redaction runs before the
bound, so truncation can never expose a fragment of a value the policy removes. A stack
trace never crosses the boundary: only the exception *type* is recorded. See
[errors-and-redaction.md](../errors-and-redaction.md).

## Sample queries

Discover the configured Axiom MCP tools in your assistant. Start with `graph_status` for the authorized solution, then search and choose an unambiguous node for a context query. Replace example scope and target values with registered IDs and values returned by the gateway. These are MCP tool arguments, not shell commands.

```json
{"solution_id":"my-solution","operation":"search","query":"OrderService","max_nodes":20,"max_bytes":8192,"consistency":"allow_stale"}
```

Use a returned node ID as the context target:

```json
{"solution_id":"my-solution","operation":"context","target":"<node-id-from-search>","projection":["identity","relations","source_locations","coverage"],"depth":1,"max_nodes":20,"max_edges":40,"max_bytes":8192,"consistency":"allow_stale"}
```

| Operation | Additional selectors and limits |
|---|---|
| `search` | `query`, `max_nodes`; no traversal projection or depth |
| `context`, `neighbors` | `target`, `depth`, `direction`, `edge_kinds`, `projection`, node/edge limits |
| `callers`, `dependencies`, `impact` | `target`, `depth`, `edge_kinds`, `projection`, node/edge limits; no direction selector |
| `path` | `target`, `target_to`, `depth`, `direction`, `edge_kinds`, `projection`, node/edge limits |
| `changes` | Optional baseline generation selector; no traversal fields |

All operations accept their common solution/project scope, consistency and byte budget. When specifying a traversal `projection`, include `source_locations`. Unexpected fields are validation errors. For a pinned read, set `consistency=pinned` and supply the observed `catalog_generation_id`; an unavailable pin is reported rather than silently replaced with current data.

Read source only at the returned locations needed to verify the answer. Report the observed generation, coverage and truncation. `allow_stale` can observe a daemon's watcher refresh without requesting reconciliation; it does not prove live-source freshness. `require_fresh` needs reconcile capability and can return `NOT_READY` with a job handle.

## Contract versioning

`query-request` and `query-response` are versioned together at `schema_version` `1`, and
the enums in this page are read from the schemas rather than restated independently. A
request field the schema does not list is rejected, so an additive field is a schema
version change and not a silent extension. If an old generation has been collected, the
gateway returns `SNAPSHOT_EXPIRED`; it does not answer from a different generation under
the old identity.

## Readiness and limits

All six handlers are registered through the gateway runtime on both public transports. Native installed-wheel checks cover protocol discovery, authenticated calls, scoped refusals and shutdown. These checks do not establish integration with a particular licensed AI-host application.

- **Generation comparisons:** `changes` reports head-side added/modified facts. Removed facts and per-kind totals are not included. Incompatible or undeclared schema majors are reported instead of compared.
- **Cursors and budgets:** queries validate incoming cursors and enforce byte budgets. The query path does not currently issue a new `next_cursor`.
- **Freshness:** response/status code projects the recorded freshness, coverage and verification evidence. A fresh claim depends on valid daemon-produced inputs, not simply on an initialized SDK.
- **Readiness:** `/readyz` separates query and control availability. A listening process alone is insufficient. Missing probes or unavailable inputs must be reported; transport-only defaults do not establish graph readiness.
- **Control operations:** reconciliation, job cancellation and verification delegate to the graph daemon and keep the caller's capability boundary. Reads can use valid snapshots while control is offline; control-dependent calls report that failure.
- **Shutdown:** in-flight work has a deadline and native read guards are released on cancellation, so streaming or a restart does not retain the guard.

See [query transports](../query-transports.md), [snapshot guide](../guides/snapshots.md) and [CLI diagnostics](../cli-version-and-doctor.md) for operation-specific failure handling.
