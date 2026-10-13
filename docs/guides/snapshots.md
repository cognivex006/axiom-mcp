# Snapshot race and read-only guide

Published graph generations are immutable. This guide explains how MCP reads them consistently while the graph daemon publishes changes or collects old data. It covers reader guarantees and the limits of cooperating processes.

## Invariants

| Guarantee | Meaning |
| --- | --- |
| Immutable generations | A published generation is immutable. A published file is never reopened for truncate or write. |
| Validated publication | The current pointer references only a generation that is complete, schema-valid and checksum-valid. |
| One generation per query | One query uses exactly one generation vector. It never re-reads `current.json` per shard and admits a second generation. |
| Durable publication recovery | A SQLite commit and a filesystem publish are not one distributed transaction; a durable outbox plus idempotent recovery bridges them. |
| Reader and collector coordination | Readers and GC must coordinate across processes. An atomic rename alone does not stop a generation being collected mid-read. |
| Explicit partial coverage | A solution catalog carries a `(project_id, generation_id, source_fingerprint)` vector for every available member; a missing project is reported `partial`, never an invented empty graph. |
| Catalog publication boundary | Publishing several repositories is not atomic; the catalog pointer is the read-model commit point, not a transaction over source repositories. |
| Deterministic content | Identical inputs, toolchain and profile produce identical canonical content bytes; timestamp and local sequence are not part of a generation's identity. |

## Pin one generation

This is the rule the rest of the guide exists to protect. A reader acquires the guard, reads
the catalog pointer **once**, and pins that generation in the request context. A
project-only query pins the project pointer once. From then on the request references one
exact generation vector, and every shard is read from that pinned generation - never from a
freshly re-read `current.json`.

A lazy-load of further shards either continues to hold the guard, or reopens it against
**the same exact generation** after re-checking availability. Switching to the latest
generation in the middle of a request is the failure mode: it is how one answer ends up
half old and half new.

The pinned generation is identified by `catalog_generation_id`, which is the lowercase
SHA-256 of the generation's canonical manifest. A pinned query that names a generation
which has already been collected returns `SNAPSHOT_EXPIRED` rather than silently reading
current data. An immutable historical generation does not imply live source freshness; the
response reports freshness separately.

## Why live publication is a race

Three facts make a naive reader wrong, and each maps to one invariant:

- **Commit and publish are two systems.** A SQLite write transaction and the filesystem
  publish are not one distributed transaction . A generation can therefore exist in
  the database and not yet on disk, or on disk and not yet pointed at. The durable outbox
  plus idempotent recovery is what makes that window recoverable.
- **A rename is not a reservation.** Moving a staging directory into place is atomic for the
  directory entry, but it does not stop the collector from removing the generation a moment
  later . Only a shared guard held across the copy coordinates the reader with GC.
- **Repositories publish independently.** A multi-repository solution is not published as
  one atomic batch . The catalog pointer is the read-model commit point; individual
  project pointers can advance while the catalog still pins an older vector, and a
  solution-level query legitimately continues to use that old vector until the catalog job
  republishes.

The writer's own ordering is designed around the same facts: parsing, SQLite transactions
and staging I/O all happen **outside** the exclusive guard, and the exclusive guard is held
only for the final checks, the current-pointer replacement, the catalog replacement, and GC
selection and deletion. No process holds a SQLite write transaction while waiting for the
guard.

## Pointer, manifest and shard closure

A published generation is three nested integrity claims, and the reader checks all three.
See [manifest-validation.md](../manifest-validation.md).

1. **Pointer.** `current.json` names exactly one generation and carries the digest of that
   generation's manifest. Its field set is exactly
   `{schema_version, generation_id, manifest_sha256}`; in V2 the generation directory name,
   the pointer's `generation_id` and the pointer's `manifest_sha256` are all the same
   string.
2. **Manifest.** The manifest lists every shard with `path`, `role`, `sha256`, `bytes` and
   `records`, and its own identity is the SHA-256 over its canonical bytes.
3. **Shard.** Each shard is pinned by the manifest entry that names it.

Canonical bytes are UTF-8 without a BOM, LF newlines, lexicographically sorted keys, compact
separators, exactly one trailing LF, no floats and no non-string keys, with Unicode
preserved as supplied. A document that is valid JSON but not canonical is refused rather
than silently re-serialised, because the digest that identifies a generation is a digest
over those exact bytes.

The order of checks is part of the contract: the schema major is checked before the
canonical form, the pointer's field set before its digest pair, and a shard's declared
length, then its digest, then its parse, then its record count - so a truncated shard is
reported as truncated and untrusted bytes never reach the JSON parser before they are known
to be the published ones.

Bounds are part of the claim: a shard whose declared byte size is outside `1..16777216`, whose digest is not a lowercase
sha256, or whose path is not portable and relative is refused as `ManifestInvalid`, and the manifest file itself is
`manifest.json`.

Paths are resolved by the trusted registry, never by the caller. A query names a logical
solution, project, lane and generation reference; an absolute POSIX path, a drive-qualified
Windows path, a UNC path, a backslash, an empty reference or any `..` segment is refused as
`UntrustedPath`, and containment is re-checked after symbolic links are resolved so a
symlink planted inside a lane cannot escape it. `.staging/` is never a read source.

## Guarded reading

`src/axiom_mcp/guard/` implements the Python side of the frozen protocol in
native reader writer guards. It contains no policy of its own: it uses the
same two lock files, the same order and the same bounded wait that the Rust side is
required to implement, so a publisher written in one language and a reader written in the
other stay safe over the same files.

The two stable lock files are `admission.lock` and `data.lock`. The acquisition order is
**admission, then data**, on every platform; the release order is the reverse. A reader
holds the guard like this:

```
acquire admission.lock  SHARED
acquire data.lock       SHARED   (while still holding admission)
release admission.lock           (early: the reader no longer needs it)
... copy the bounded pointer/manifest/shard bytes from the pinned generation ...
release data.lock
... parse and respond (the guard is no longer held) ...
```

`SolutionGuard.reader()` is the context manager for that shape; `SolutionGuard.publisher()`
takes both locks **exclusive** for the bounded publication or guarded-deletion phase. A
publisher under the guard performs only the bounded catalog/pointer publication or guarded
deletion phase. Analysis, staging writes and SQLite work happen outside it.

The rules the engine enforces once, identically on both platforms:

| Rule | Value |
| --- | --- |
| Acquisition order | `admission.lock`, then `data.lock` |
| Release order | `data.lock`, then `admission.lock` |
| Default acquire timeout | `5000` ms |
| Maximum single wait | `60000` ms |
| Retry interval | `25` ms, doubling to a `250` ms cap |
| Cancellation | supported, checked between attempts |
| Lock upgrade | not permitted - release and retry from the start |
| Recursive acquire | not permitted in one execution path |

A timeout or cancellation releases every already-acquired guard in reverse acquisition
order and reports a bounded lock-timeout; the kernel is not assumed to be starvation-free.
Locks are OS-owned, not content-owned: a crashed process releases them because the OS does,
a lock file's contents never prove ownership, and the stable empty lock files stay in place -
removing them is a separate maintenance action that requires every participating process to
have stopped. A path whose identity changed between open and the identity check is reopened
rather than locked as if it were the named file.

## Generations and immutability

A published generation under `generations/<generation-id>/` is immutable . The
directory name is the generation's own manifest digest, so a directory that does not hold
the digest it is named for is refused before the pointer is trusted. Re-publishing an
existing generation verifies its contents match before reusing it instead of overwriting it.
The live and checkpoint lanes each hold their own `current.json`, `generations/` and
`.staging/`, and staging always lives on the same filesystem or volume as the generation and
pointer it will publish.

## Recovery and bounded retry

If a required generation is missing or corrupt, the reader drops the partial request and
retries the whole catalog read within a bounded budget. If it still cannot complete, it
returns `SNAPSHOT_UNAVAILABLE` or `SNAPSHOT_CORRUPT` and requests reconciliation through the
control plane according to policy.

| Condition | Code | Retryable | HTTP |
| --- | --- | --- | --- |
| Required generation cannot be read within the retry budget | `SNAPSHOT_UNAVAILABLE` | yes | 503 |
| A file is present but fails validation | `SNAPSHOT_CORRUPT` | no | 500 (unnamed by the control contract) |
| A pinned generation has been collected | `SNAPSHOT_EXPIRED` | no | 410 |

**The reader never answers half old and half new.** A failed query carries no partial result
envelope; it carries the error variant. `SNAPSHOT_UNAVAILABLE` is retryable because the
generation may reappear; `SNAPSHOT_EXPIRED` is not, because continuing against current data
would answer a different question under the old identity.

## Retention and GC

The default `live` policy keeps at least the current plus the previous two generations with a
ten-minute grace, and it is a tunable policy rather than a performance guarantee. GC never
deletes the current generation, checkpoint references, an active catalog vector or pending
outbox references, no matter how full the budget is. GC acquires the exclusive guard before
revalidating and deleting, and a shared reader that is still loading keeps it waiting;
loaded in-memory caches must not depend on a file after the guard is released. A
generation's mtime alone never decides liveness.

When the disk quota is full, publication pauses and optional caches are shed, and the
component reports the condition instead of deleting the current generation to make room.
Size is checked before staging, with headroom reserved for the old and new generation
together. Checkpoint-lane pruning happens only during an explicit checkpoint or update
command and only for generated, owned, unreferenced files; live background GC never modifies
tracked checkpoint files.

## The checkpoint lane

Each lane has a `live/` and a `checkpoint/` directory. The checkpoint lane is a durable,
usually Git-tracked record; the live lane is the working set a query answers from. A
snapshot-only gateway can read a checkpoint with the local `SolutionGuard` and no graphd
process running, which is why an offline checkpoint lane resolves exactly like a live one.

## Crash recovery

The protocol fixes a recovery outcome for each crash point, so recovery is a decision rather
than a guess:

| Crash point | What remains | Recovery |
| --- | --- | --- |
| Before the DB commit | Leased job, dirty generation | The lease expires and the job retries. |
| After the DB commit, before staging | `PREPARED` outbox | Regenerate the exact revision or mark it superseded as a checked action. |
| While writing shards | Orphan staging | Validate and continue if supported, or delete owned staging and re-export. |
| After a generation completes, before the pointer | Immutable orphan | Verify and reuse; it is not served yet. |
| After the pointer replace, before the outbox ack | New pointer, old outbox | Verify hash and fencing order, then ack; do not roll back to the old generation. |
| A project published while the catalog is old | New project current, catalog still pins the old vector | The solution query continues on the old vector while the catalog job retries. |
| During GC | Some unreferenced files already deleted | Idempotent retry; the current generation and catalog are never touched. |
| Pointer corrupt after an external edit | Invalid hash | Quarantine, report and reconcile; never guess the generation. |

## Raw multi-file reads are not safe

Reading the JSON files directly is allowed, but it is **not** the same guarantee. A raw
reader that opens several files itself holds no reader registration and no lock, so nothing
coordinates it with GC or with a Git checkout, and it is not covered by the coordinated
reader availability guarantee. It is not safe to reconstitute a multi-file answer by
opening `current.json` again for each file - that is exactly the mixed-generation read failure.

A direct reader must pin one immutable generation, validate its manifest and shard hashes, and report missing data instead of mixing generations. Assistant queries should use MCP tools, whose reader holds the shared guard during the bounded copy. MCP does not expose a snapshot-reader CLI subcommand.

## Guarantees and limits

- The native guard engine ships both `locks_posix.py` (`flock`, whole-file) and `locks_windows.py` (`LockFileEx`, byte range offset 0 length 1). `engine.py`, `protocol.py` and `adapter.py` share the reader protocol. The Windows backend is present at this revision; platform-specific tests exercise the primitive on its native OS.
- Cross-language guard tests require an ABI-compatible native holder configured with `AXIOM_GUARD_HOLDER_ARGV`. A skipped holder test is not interoperability evidence.
- `read_session.py` copies the pointer, manifest and required shards under the shared guard and releases it before response work. `cache.py` keys entries by immutable generation identity. `manifest.py` validates bytes and `registry.py` resolves registered paths.
- The graph daemon owns publication, retention, garbage collection and outbox recovery. MCP does not implement that writer lifecycle.
- Third-party processes such as Git, editors and antivirus tools do not necessarily hold the guard. Missing or changed data is still a recoverable failure, not permission to combine generations.
- Windows x64, Linux x64 and Intel Mac use native platform guards. See [runtime compatibility](../runtime-compatibility.md) for Python requirements and deferred targets. Protocol verification does not establish integration with every assistant product.
