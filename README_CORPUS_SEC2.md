# Corpus secundario C2 — stdlib / iteración

Agente C2, token frente PITONF-C2B0F132.
Corpus independiente del primario, enfocado en `itertools`, `collections`, `functools`, `operator`, ficheros, `os.path`/`pathlib`, `json`, `math`.

## Ámbito
- `corpus_sec/stdlib/*.py` — 20 scripts deterministas, sin aleatoriedad, sin fechas, sin rutas absolutas.
- `tools/parity_corpus_sec2.py` — runner propio, criterios HONESTOS (stdout byte a byte + exit code; o ambos excepciones).
- No se toca `piton/`, `corpus_v4/`, `tools/parity_corpus_v4.py`, `tools/tier_b_divergence_map.py`, `tests/`, `corpus_sec/expr/`, `tools/parity_corpus_sec.py`.

## Ejecución
```bash
cd "/home/danny/Development/ISyCo Git/PITON"
TMPDIR=/tmp/piton-c2 python3 tools/parity_corpus_sec2.py --json /tmp/piton-c2/sec_stdlib.json
```

## Qué NO cubre
- No cubre expresiones/ tipos/ formato (eso es C1/expr).
- No cubre `async`, `io` avanzado, red, sockets, `subprocess`, `multiprocessing`.
- No cubre rutas absolutas ni operaciones dependientes de hora/sistema.
- No cubre `os.path` con rutas absolutas; solo paths relativos creados en tmp.
