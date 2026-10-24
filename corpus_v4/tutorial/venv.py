# corpus_v4 tutorial page: venv
# source: https://docs.python.org/3.12/tutorial/venv.html
# sha256(HTML): 5084bc3c3f878a6986a8bd1034db1acd74ae69443ce8b13c4780fd732ed3c474
# ejemplos doctest: 2 (excluidos 0 por sintaxis rota intencional)
# convertido por tools/parity_corpus_v4.py fetch: cada ejemplo queda en
# try/except BaseException (semantica REPL: la sesion no muere en un error)
try:
    import sys
except BaseException:
    print('EXC')
try:
    sys.path
except BaseException:
    print('EXC')
