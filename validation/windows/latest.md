# Windows validation

- Status: **FAIL**
- Run: $RunId
- Commit: $gitCommit
- Computer: $(DANNY)
- Python: $pythonExecutable
- Worktree dirty before run: $(?? piton/native_runtime.o
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
?? validation/windows/windowsvalidate-20260922T132805Z.log.Length -gt 0)

## Steps

| Step | Status | Exit | Seconds |
|---|---|---:|---:|
| python-version | PASS | 0 | 0.067 |
| windows-focused-corpus | PASS | 0 | 27.035 |
| windows-regression-suite | FAIL | 1 | 935.248 |

Failed: windows-regression-suite

Raw output: [$ArchiveStem.log](./windowsvalidate-20260922T132805Z.log)
Machine-readable receipt: [$ArchiveStem.json](./windowsvalidate-20260922T132805Z.json)
