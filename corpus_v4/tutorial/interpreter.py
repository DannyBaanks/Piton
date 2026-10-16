# corpus_v4 tutorial page: interpreter
# source: https://docs.python.org/3.12/tutorial/interpreter.html
# sha256(HTML): 17c6accfe225ccf64cd5016e7ca6c5ff44ca04975d2134b5707a91551b753f1f
# ejemplos doctest: 2 (excluidos 1 por sintaxis rota intencional)
# convertido por tools/parity_corpus_v4.py fetch: cada ejemplo queda en
# try/except BaseException (semantica REPL: la sesion no muere en un error)
try:
    the_world_is_flat = True
except BaseException:
    print('EXC')
