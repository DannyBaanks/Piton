# corpus_v4 tutorial page: modules
# source: https://docs.python.org/3.12/tutorial/modules.html
# sha256(HTML): 777bbc075de875d592bca8b1624a3cceead80c0e72ca41a76e651bdc33cc69ef
# ejemplos doctest: 30 (excluidos 0 por sintaxis rota intencional)
# convertido por tools/parity_corpus_v4.py fetch: cada ejemplo queda en
# try/except BaseException (semantica REPL: la sesion no muere en un error)
try:
    import fibo
except BaseException:
    print('EXC')
try:
    fibo.fib(1000)
except BaseException:
    print('EXC')
try:
    fibo.fib2(100)
except BaseException:
    print('EXC')
try:
    fibo.__name__
except BaseException:
    print('EXC')
try:
    fib = fibo.fib
except BaseException:
    print('EXC')
try:
    fib(500)
except BaseException:
    print('EXC')
try:
    from fibo import fib, fib2
except BaseException:
    print('EXC')
try:
    fib(500)
except BaseException:
    print('EXC')
try:
    from fibo import *
except BaseException:
    print('EXC')
try:
    fib(500)
except BaseException:
    print('EXC')
try:
    import fibo as fib
except BaseException:
    print('EXC')
try:
    fib.fib(500)
except BaseException:
    print('EXC')
try:
    from fibo import fib as fibonacci
except BaseException:
    print('EXC')
try:
    fibonacci(500)
except BaseException:
    print('EXC')
try:
    import fibo
except BaseException:
    print('EXC')
try:
    import sys
except BaseException:
    print('EXC')
try:
    sys.ps1
except BaseException:
    print('EXC')
try:
    sys.ps2
except BaseException:
    print('EXC')
try:
    sys.ps1 = 'C> '
except BaseException:
    print('EXC')
try:
    import sys
except BaseException:
    print('EXC')
try:
    sys.path.append('/ufs/guido/lib/python')
except BaseException:
    print('EXC')
try:
    import fibo, sys
except BaseException:
    print('EXC')
try:
    dir(fibo)
except BaseException:
    print('EXC')
try:
    dir(sys)
except BaseException:
    print('EXC')
try:
    a = [1, 2, 3, 4, 5]
except BaseException:
    print('EXC')
try:
    import fibo
except BaseException:
    print('EXC')
try:
    fib = fibo.fib
except BaseException:
    print('EXC')
try:
    dir()
except BaseException:
    print('EXC')
try:
    import builtins
except BaseException:
    print('EXC')
try:
    dir(builtins)
except BaseException:
    print('EXC')
