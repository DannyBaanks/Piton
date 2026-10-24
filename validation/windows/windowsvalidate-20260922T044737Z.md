# Windows validation

- Status: **FAIL**
- Run: $RunId
- Commit: $gitCommit
- Computer: $(DANNY)
- Python: $pythonExecutable
- Worktree dirty before run: $(?? piton/native_runtime.o
?? validation/windows/latest.json
?? validation/windows/latest.log
?? validation/windows/latest.md
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
?? validation/windows/windowsvalidate-20260922T044737Z.log.Length -gt 0)

## Steps

| Step | Status | Exit | Seconds |
|---|---|---:|---:|
| tool-nasm | FAIL | 1 | 0 |
| tool-gcc | FAIL | 1 | 0 |
| python-version | PASS | 0 | 0.081 |
| windows-focused-corpus | FAIL | 1 | 0.752 |
| windows-regression-suite | FAIL | 1 | 5.175 |

Failed: tool-nasm, tool-gcc, windows-focused-corpus, windows-regression-suite

Raw output: [$ArchiveStem.log](./windowsvalidate-20260922T044737Z.log)
Machine-readable receipt: [$ArchiveStem.json](./windowsvalidate-20260922T044737Z.json)
