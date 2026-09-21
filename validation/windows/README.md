# Windows validation results

This folder is the GitHub handoff point for the Windows machine.

Run from the repository root on Windows:

```powershell
.\windowsvalidate.ps1 -FullSuite -Publish
```

The script writes these files here:

- `latest.json`: machine-readable receipt for the newest run.
- `latest.md`: short human-readable summary.
- `latest.log`: raw command output.
- `windowsvalidate-<UTC timestamp>.*`: immutable history for that run.

`-Publish` stages only files under this folder, commits them, and pushes the
current branch to `origin`. It never stages implementation changes elsewhere in
the checkout. A failing validation is still published so the other machine can
see the failure and its raw output.

The receipt records the exact commit, host, tool versions, commands, exit codes,
and worktree state. Windows evidence does not claim Linux parity; combine it
with the Linux receipt before closing a cross-platform gate.
