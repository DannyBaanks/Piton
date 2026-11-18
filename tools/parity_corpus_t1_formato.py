#!/usr/bin/env python3
"""Runner for corpus_t1/formato parity vs CPython."""
import json, hashlib, subprocess, sys, tempfile, os
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
CORPUS_DIR = REPO / "corpus_t1" / "formato"
EXPECTED_JSON = REPO / "corpus_t1" / "expected" / "formato.json"
from piton.linux_x86 import compile_native_linux
from parity_corpus_v4 import python_to_piton, _read_source

def run_cpython(path):
    proc = subprocess.run([sys.executable, str(path)], capture_output=True, timeout=5)
    return proc.stdout, proc.stderr, proc.returncode

def run_piton(src_piton):
    with tempfile.TemporaryDirectory(prefix="piton-t1-") as d:
        out_path = Path(d) / "prog"
        try:
            exe = compile_native_linux(src_piton, out_path)
        except Exception as e:
            return b"", str(e).encode(), -1
        proc = subprocess.run([str(exe)], capture_output=True, timeout=5)
        return proc.stdout, proc.stderr, proc.returncode

def main():
    expected = json.loads(EXPECTED_JSON.read_text(encoding='utf-8'))
    exp_by_file = {e['file']: e for e in expected}
    total = 0
    equiv = 0
    diverges = []
    for f in sorted(CORPUS_DIR.glob('*.py')):
        total += 1
        name = f.name
        src = f.read_text(encoding='utf-8')
        sha = hashlib.sha256(src.encode('utf-8')).hexdigest()
        exp = exp_by_file.get(name)
        if not exp:
            diverges.append({"file": name, "reason": "no expected"})
            continue
        # verify sha
        if exp['sha256'] != sha:
            diverges.append({"file": name, "reason": "sha mismatch"})
            continue
        # measure PITON
        try:
            piton_src = python_to_piton(src)
        except Exception as e:
            diverges.append({"file": name, "reason": "adapt fail", "detail": str(e)})
            continue
        try:
            out_p, err_p, rc_p = run_piton(piton_src)
        except Exception as e:
            diverges.append({"file": name, "reason": "piton run fail", "detail": str(e)})
            continue
        out_e = exp['stdout'].encode('utf-8')
        err_e = exp['stderr'].encode('utf-8')
        rc_e = exp['exit_code']
        # compare byte to byte stdout + exit code
        if rc_p == rc_e and out_p == out_e:
            # if both have exception? For simplicity, treat stderr diff as diverge?
            # Expect both stderr empty for our examples
            if err_p == err_e:
                equiv += 1
                continue
        # record diverge
        diverges.append({
            "file": name,
            "expected_rc": rc_e,
            "piton_rc": rc_p,
            "expected_out": exp['stdout'],
            "piton_out": out_p.decode('utf-8', errors='replace'),
            "expected_err": exp['stderr'],
            "piton_err": err_p.decode('utf-8', errors='replace')
        })
    result = {
        "total": total,
        "equiv": equiv,
        "diverge": len(diverges),
        "divergences": diverges[:20]  # limit
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # also write output file
    out_file = REPO / "corpus_t1" / "parity_formato.json"
    out_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0

if __name__ == "__main__":
    sys.exit(main())
