# Native MCP verification

The portable CI workflow builds one source-bound wheel and installs the same wheel on Windows x64, Linux x64 and Intel Mac. Each native lane verifies installed imports, six-tool discovery, stdio/HTTP protocol calls, scoped authentication refusals, shutdown and native reader/writer exclusion. These tests do not claim that a licensed AI-host application was exercised.

`portable_readiness.py --help` lists the exact wheel, interpreter, core CLI, source revision and guard-proof inputs. The isolated install must import from its wheel environment. `build_rust_holder.py` supplies a native Rust guard holder; Python guards must refuse while its exclusive lock is held and recover after process termination.

Local process investigations use `capture_native_matrix.py --help` and `installed_guard_probe.py`. Record the actual kernel/platform, source and input hashes, sanitized argv, expected and observed exit codes. Containers, WSL and native Windows execution remain distinct records. Historical candidate identities and archived run instructions stay in internal evidence.

Native runtime availability and GitHub Release publication are separate outcomes. Unrun platforms remain unverified; a source checkout or passing schema check is insufficient installed-wheel evidence.
