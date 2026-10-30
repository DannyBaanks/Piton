# AutoPiton Safety

AutoPiton runs untrusted, machine-generated Python. The sandbox is **fail
closed**: if verified isolation is unavailable, nothing executes. This document
states exactly what is guaranteed, how each guarantee is tested, and what is
*not* claimed.

## Mechanism (bubblewrap)

Every execution uses `bwrap --unshare-all --die-with-parent --new-session`:

- `--unshare-all`: no network, PID, IPC, UTS, cgroup or user sharing with the
  host. Network egress and host-process visibility are gone by construction.
- Read-only system view: `/usr`, `/lib`, `/lib64` are `--ro-bind`; the interpreter
  and `gcc` are reachable but not writable.
- Writable workdir: the temp dir is bound at `/work`; `--chdir /work`. `--tmpfs
  /tmp` keeps `/tmp` writes inside the namespace, never on the host.
- `--clearenv` plus a fixed `PATH/HOME/TMPDIR/LANG/LC_ALL`; the program cannot
  read host environment secrets.
- PITON repo is bound read-only at `/piton-repo` for the build worker; the worker
  cannot write the repository.
- Native payloads run in a **separate minimal root** (`--tmpfs /` + only the
  static ELF, `prlimit`, and system libs), not the Python root.

## Resource limits

Applied *inside* the namespaces with `/usr/bin/prlimit` (not host `preexec_fn`,
which made `clone` fail with EAGAIN before the user namespace existed):

| Limit | Source |
|---|---|
| `--as` (address space) | `memory_limit_mb` (default 256; build worker 1024) |
| `--cpu` | timeout seconds (+1), or `cpu_limit_seconds` |
| `--nofile` | `max_open_files` (default 64) |
| `--fsize` | `file_size_limit_bytes` (default 8 MB; build worker 64 MB) |
| `--core=0` | no core dumps |
| `--nproc` | `max_processes` (default 256) |

## Process control

- Output is drained by bounded threads (`max_output_bytes`, default 1 MB); over
  that, `output_truncated` is set and the rest is dropped — no host OOM.
- On timeout the whole process **group** is `SIGKILL`ed (`start_new_session=True`
  + `os.killpg`), so child processes die with the parent.
- Workdir is `tempfile.mkdtemp` and removed on context exit (`keep_workdir=False`).
- Every file the program leaves in `/work` (excluding its own inputs) is hashed
  into `fs_created` for the audit trail.

## Isolation canaries (A–J, all run through the real bwrap sandbox)

There is no mocked execution path. `tests/test_sandbox.py`:

| ID | Guarantee | Test |
|---|---|---|
| A | normal run is clean & structured | `test_a_normal_run` |
| A2 | runtime exception captured (not swallowed) | `test_a2_runtime_exception_is_structured` |
| A3 | syntax error captured | `test_a3_syntax_error_is_structured` |
| B | infinite loop hits the wall-clock timeout | `test_b_infinite_loop_times_out` |
| C | memory ceiling enforced | `test_c_memory_limit` |
| D | write to `/usr` denied and absent on host | `test_d_write_outside_workdir_is_denied` |
| D2 | `/tmp` write stays in namespace | `test_d2_tmp_writes_stay_inside_namespace` |
| E | outbound socket fails (no network) | `test_e_network_is_blocked` |
| F | spawned `sleep 54321` left no survivor after timeout | `test_f_process_tree_is_killed_on_timeout` |
| G | host env cleared to the fixed allow-list | `test_g_environment_is_cleared` |
| H | filesystem side effects recorded | `test_h_filesystem_changes_are_recorded` |
| I | output bounded at the byte cap | `test_i_output_is_truncated` |
| J | children run but remain isolated | `test_j_children_are_allowed_but_isolated` |

A `pgrep` in canary F confirms the escaping `sleep` was actually reaped; canary D
asserts `/usr/autopiton_pwned` does **not** exist on the host after the sandboxed
program "tried" to write it.

## Known limits (NOT claimed)

- **Not a security boundary against a determined local attacker.** bubblewrap
  with user namespaces is a strong containment for fuzz workloads, but the host
  kernel's namespace/ptrace surface is out of scope. Do not run AutoPiton on
  secrets-bearing hosts expecting confidentiality guarantees.
- **Linux only.** `sandbox_available()` gates everything; on any other platform
  or without usable user namespaces, `compare`/`generate` execution exits 2
  rather than degrading to an unsandboxed run.
- **Limits are coarse.** `prlimit --as` bounds address space, not peak RSS
  precisely; a program can be killed before it prints a clean MemoryError, which
  surfaces as `crash`/`INCONCLUSIVE`, not a divergence.
- **Shared filesystem metadata** (inode/timestamp) of the read-only binds is
  visible inside the namespace; only write effects are contained.

## Refusal behavior

`sandbox_available()` actually launches `bwrap … /usr/bin/true` as a probe. If it
fails, `Sandbox.__enter__` raises `SandboxUnavailable` and the CLI's
`_require_sandbox()` exits with code 2. AutoPiton never falls back to running a
generated program on the host.
