# corpus_v4 tutorial page: errors
# source: https://docs.python.org/3.12/tutorial/errors.html
# sha256(HTML): 812f08c04914c5d8d42991d89d59b741d78195a70cd137347b4af7cc6b2fec92
# ejemplos doctest: 33 (excluidos 22 por sintaxis rota intencional)
# convertido por tools/parity_corpus_v4.py fetch: cada ejemplo queda en
# try/except BaseException (semantica REPL: la sesion no muere en un error)
try:
    10 * (1/0)
except BaseException:
    print('EXC')
try:
    4 + spam*3
except BaseException:
    print('EXC')
try:
    '2' + 2
except BaseException:
    print('EXC')
try:
    raise NameError('HiThere')
except BaseException:
    print('EXC')
try:
    bool_return()
except BaseException:
    print('EXC')
try:
    divide(2, 1)
except BaseException:
    print('EXC')
try:
    divide(2, 0)
except BaseException:
    print('EXC')
try:
    divide("2", "1")
except BaseException:
    print('EXC')
try:
    f()
except BaseException:
    print('EXC')
try:
    excs = []
except BaseException:
    print('EXC')
try:
    raise ExceptionGroup('We have some problems', excs)
except BaseException:
    print('EXC')
