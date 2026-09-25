# Windows validation

- Status: **FAIL**
- Run: $RunId
- Commit: $gitCommit
- Computer: $(DANNY)
- Python: $pythonExecutable
- Worktree dirty before run: $(M  validation/windows/latest.json
M  validation/windows/latest.log
M  validation/windows/latest.md
A  validation/windows/windowsvalidate-20260925T123622Z.json
A  validation/windows/windowsvalidate-20260925T123622Z.log
A  validation/windows/windowsvalidate-20260925T123622Z.md
?? piton/native_runtime.o
?? validation/windows/windowsvalidate-20260922T012102Z.json
?? validation/windows/windowsvalidate-20260922T012102Z.log
?? validation/windows/windowsvalidate-20260922T012102Z.md
?? validation/windows/windowsvalidate-20260922T013816Z.json
?? validation/windows/windowsvalidate-20260922T013816Z.log
?? validation/windows/windowsvalidate-20260922T013816Z.md
?? validation/windows/windowsvalidate-20260922T014701Z.json
?? validation/windows/windowsvalidate-20260922T014701Z.log
?? validation/windows/windowsvalidate-20260922T014701Z.md
?? validation/windows/windowsvalidate-20260922T022313Z.json
?? validation/windows/windowsvalidate-20260922T022313Z.log
?? validation/windows/windowsvalidate-20260922T022313Z.md
?? validation/windows/windowsvalidate-20260922T045738Z.json
?? validation/windows/windowsvalidate-20260922T045738Z.log
?? validation/windows/windowsvalidate-20260922T045738Z.md
?? validation/windows/windowsvalidate-20260922T132805Z.json
?? validation/windows/windowsvalidate-20260922T132805Z.log
?? validation/windows/windowsvalidate-20260922T132805Z.md
?? validation/windows/windowsvalidate-20260925T123707Z.log.Length -gt 0)

## Steps

| Step | Status | Exit | Seconds |
|---|---|---:|---:|
| python-version | PASS | 0 | 0.053 |
| windows-focused-corpus | PASS | 0 | 41.786 |
| windows-regression-suite | FAIL | 1 | 1428.602 |

Failed: windows-regression-suite

Raw output: [$ArchiveStem.log](./windowsvalidate-20260925T123707Z.log)
Machine-readable receipt: [$ArchiveStem.json](./windowsvalidate-20260925T123707Z.json)
