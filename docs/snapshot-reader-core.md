# Snapshot reader core

The reader pins a published generation, validates its manifest and shards, and copies bounded data under the instance guard before answering. It does not substitute a newer generation when the requested one is unavailable.

## Windows shared guard (`src/axiom_mcp/guard/locks_windows.py`)

The native guard package shares `engine.py` and `protocol.py` across platforms. POSIX uses `flock`; Windows uses `LockFileEx` through `locks_windows.py`. The backend changes the native primitive, not the reader protocol.

The frozen block is implemented literally:

| Item | Value |
| --- | --- |
| Primitive | `LockFileEx` |
| Byte range | offset `0`, length `1` |
| Open mode | `OPEN_ALWAYS` |
| Access | `GENERIC_READ \| GENERIC_WRITE` |
| Share mode | `FILE_SHARE_READ \| FILE_SHARE_WRITE` (excludes `FILE_SHARE_DELETE`) |
| Contention flag | `LOCKFILE_FAIL_IMMEDIATELY` |
| Exclusive flag | `LOCKFILE_EXCLUSIVE_LOCK` |
| Release | matching `UnlockFileEx`, then close the handle |

Nothing else is platform-specific. Admission before data, the reverse release order, the
bounded cancellable wait, the refusal to upgrade a held lock and the refusal to acquire the
same guard twice in one execution path live once in `engine.py`, so the two platforms cannot
drift apart in the parts a caller can observe. Only the primitive differs, because Windows has
no `flock`; a Python-only or Rust-only lock would be invisible to the other language, which is
why the family is named in the contract rather than left to the implementation.

Consequences worth knowing when reading the code:

* **Contention is control flow, not an error.** A lost non-blocking attempt raises
  `GuardBusy`, which the engine catches and retries inside the deadline. It never reaches a
  caller; a caller sees `GuardTimeout` only when the bounded budget is actually exhausted.
* **Handle sharing is part of the guarantee.** Because the share mode excludes
  `FILE_SHARE_DELETE`, a second opener that asks for delete access, or for a share mode that
  conflicts with the holder's, fails with `ERROR_SHARING_VIOLATION`, and the held file cannot
  be removed or replaced while a holder has it open. That is what keeps the lock files stable
  under a concurrent publisher.
* **Identity comes from the descriptor.** `WindowsLockHandle.identity()` adopts the Win32
  handle as a C run-time descriptor and reads `os.fstat`, producing the same device-plus-file
  index value `protocol.file_identity` produces for the path. The engine compares the two, so a
  file replaced between open and lock is re-opened rather than locked as if it were the named
  file.
* **No wrapper package.** The primitive is four `kernel32` calls reached through `ctypes`.
  Adding a dependency for them would put a lock that both languages must share behind a package
  registry the release path does not otherwise need, and the guard must work in a snapshot-only
  gateway with no daemon running.

`tests/test_guard_windows.py` runs on Windows only and is skipped where `msvcrt` is absent, so
the Windows backend is never inferred from a POSIX run. Two-process exclusion is
observed by launching `python -m axiom_mcp.guard.interop` as a child rather than by comparing
two objects in one interpreter, because a lock that only excludes itself proves nothing.

## Pinned catalog vector (`src/axiom_mcp/catalog.py`)

The catalog is the read-model commit point, so a query resolves it once and then reads every
member from the exact generation that vector names. `load_solution_catalog(location)` reads the
lane pointer a single time, loads `generations/<generation_id>/manifest.json`, and refuses a
directory whose name does not equal the digest of the catalog bytes inside it. The pointer's
`generation_id` is checked against those same bytes, so a catalog renamed under an unchanged
directory, or a pointer that quotes a digest the bytes do not have, is rejected rather than
read.

A member is exactly `(project_id, generation_id, source_fingerprint)`. The reader enforces the
exact key set and rejects a member that carries `name`, `project_name`, `path` or `directory`,
because a member resolved by name has no pinned generation and would have to be matched to a
project's newest generation - which is the latest-per-project fallback
the catalog reader prohibits.
Duplicate `project_id` members, a non-canonical byte form and an unknown schema major are all
refused, the major before the byte form so a document that is both wrong reports its version.

`pin_catalog(catalog, open_member=...)` turns the validated catalog into a `CatalogVector`.
The default opener `project_member_opener(location_for)` reads
`generations/<generation_id>/manifest.json` for the member - never the project lane's
`current.json` - and requires the manifest to hash to the pinned `generation_id` and to declare
the pinned `source_fingerprint`. A member whose generation is absent, unreadable or not the
pinned one is recorded as `missing` with a reason; it does not fail the whole read and it is
never silently substituted. The vector is then reported `partial`, and
`require_complete()` raises `CatalogMemberMissing` so a caller under `require_complete_solution`
rejects the answer explicitly instead of receiving a partial graph that looks complete.

The regression that matters most is
`test_pinned_vector_ignores_a_newer_project_generation`: a newer, fully valid generation is
published into a project lane and that lane's `current.json` is repointed at it, yet the answer
stays on the catalog's generation. A reader that consulted the lane pointer would break both
the query identity and its single-generation rule. Negative cases cover a member with
no `generation_id`, a member resolved by name, a duplicate member, an altered pinned
generation (reported `partial`, not substituted), an unknown major, non-canonical bytes, a
renamed generation directory and an absent catalog pointer.

`tests/fixtures/solution/demo-solution/` is vendored byte-for-byte from
`tests/fixtures/solution/demo-solution`; the test asserts each vendored
manifest still hashes to the generation directory that holds it.

## Bounded shard load (`src/axiom_mcp/shards.py`)

A missing or corrupt required generation invalidates the partial request. Recovery retries the whole catalog within its budget, then reports `SNAPSHOT_UNAVAILABLE` or `SNAPSHOT_CORRUPT` if it still cannot read. A collected pin reports `SNAPSHOT_EXPIRED`. Snapshot-only reads report `freshness=unknown`; a missing project remains unavailable or partial according to the selected policy.

`read_with_recovery` runs a caller-supplied loader that pins one exact catalog vector. Each
attempt returns the whole vector or raises, so a failed attempt leaves nothing behind and a
served answer can never mix two attempts. Attempts are bounded by `RecoveryLimits`; the retry
applies to the recoverable codes (missing member, unavailable or corrupt generation) and never to
`SNAPSHOT_EXPIRED`, because reading again cannot bring a collected generation back.

Policy decides a partial vector: under `allow_partial` it is served as `partial` with the members
that resolved, and under `require_complete` it is refused as `PROJECT_UNAVAILABLE` with no data at
all. Freshness is never fabricated: snapshot-only mode forces `freshness=unknown` and refuses a
caller-supplied live reading, and a `pinned`-consistency read is treated the same way because an
immutable historical generation is not evidence that the source is fresh.

`require_generation`/`classify_absence` make the archived case honest: if the lane still names the
pinned generation, its absence is `SNAPSHOT_UNAVAILABLE`; if the lane has moved on to a different
generation, the pinned one was collected and the answer is `SNAPSHOT_EXPIRED`, never the newer
data.

`tests/test_recovery.py` runs against the copied `demo-solution` bundle and proves the positive
path (a missing member is partial or refused by policy, and a flaky read recovers on the bounded
retry), the freshness rule (snapshot-only reports `unknown` and rejects a fabricated live value),
and the negative/boundary ones (a corrupt or unavailable generation retried then reported with no
partial data, a superseded generation expired on the first attempt with the newer lane data
unused, an absent-while-still-pinned generation reported unavailable, and invalid
configurations/loaders refused).
