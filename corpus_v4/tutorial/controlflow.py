# corpus_v4 tutorial page: controlflow
# source: https://docs.python.org/3.12/tutorial/controlflow.html
# sha256(HTML): 427b9a7efd964a059824fd290770e1e51c1160e7d065fc2b3e6705aaf70d91e5
# ejemplos doctest: 69 (excluidos 25 por sintaxis rota intencional)
# convertido por tools/parity_corpus_v4.py fetch: cada ejemplo queda en
# try/except BaseException (semantica REPL: la sesion no muere en un error)
try:
    x = int(input("Please enter an integer: "))
except BaseException:
    print('EXC')
try:
    words = ['cat', 'window', 'defenestrate']
except BaseException:
    print('EXC')
try:
    list(range(5, 10))
except BaseException:
    print('EXC')
try:
    list(range(0, 10, 3))
except BaseException:
    print('EXC')
try:
    list(range(-10, -100, -30))
except BaseException:
    print('EXC')
try:
    a = ['Mary', 'had', 'a', 'little', 'lamb']
except BaseException:
    print('EXC')
try:
    range(10)
except BaseException:
    print('EXC')
try:
    sum(range(4))  # 0 + 1 + 2 + 3
except BaseException:
    print('EXC')
try:
    fib(2000)
except BaseException:
    print('EXC')
try:
    fib
except BaseException:
    print('EXC')
try:
    f = fib
except BaseException:
    print('EXC')
try:
    f(100)
except BaseException:
    print('EXC')
try:
    fib(0)
except BaseException:
    print('EXC')
try:
    print(fib(0))
except BaseException:
    print('EXC')
try:
    f100 = fib2(100)    # call it
except BaseException:
    print('EXC')
try:
    f100                # write the result
except BaseException:
    print('EXC')
try:
    function(0, a=0)
except BaseException:
    print('EXC')
try:
    standard_arg(2)
except BaseException:
    print('EXC')
try:
    standard_arg(arg=2)
except BaseException:
    print('EXC')
try:
    pos_only_arg(1)
except BaseException:
    print('EXC')
try:
    pos_only_arg(arg=1)
except BaseException:
    print('EXC')
try:
    kwd_only_arg(3)
except BaseException:
    print('EXC')
try:
    kwd_only_arg(arg=3)
except BaseException:
    print('EXC')
try:
    combined_example(1, 2, 3)
except BaseException:
    print('EXC')
try:
    combined_example(1, 2, kwd_only=3)
except BaseException:
    print('EXC')
try:
    combined_example(1, standard=2, kwd_only=3)
except BaseException:
    print('EXC')
try:
    combined_example(pos_only=1, standard=2, kwd_only=3)
except BaseException:
    print('EXC')
try:
    foo(1, **{'name': 2})
except BaseException:
    print('EXC')
try:
    foo(1, **{'name': 2})
except BaseException:
    print('EXC')
try:
    concat("earth", "mars", "venus")
except BaseException:
    print('EXC')
try:
    concat("earth", "mars", "venus", sep=".")
except BaseException:
    print('EXC')
try:
    list(range(3, 6))            # normal call with separate arguments
except BaseException:
    print('EXC')
try:
    args = [3, 6]
except BaseException:
    print('EXC')
try:
    list(range(*args))            # call with arguments unpacked from a list
except BaseException:
    print('EXC')
try:
    d = {"voltage": "four million", "state": "bleedin' demised", "action": "VOOM"}
except BaseException:
    print('EXC')
try:
    parrot(**d)
except BaseException:
    print('EXC')
try:
    f = make_incrementor(42)
except BaseException:
    print('EXC')
try:
    f(0)
except BaseException:
    print('EXC')
try:
    f(1)
except BaseException:
    print('EXC')
try:
    pairs = [(1, 'one'), (2, 'two'), (3, 'three'), (4, 'four')]
except BaseException:
    print('EXC')
try:
    pairs.sort(key=lambda pair: pair[1])
except BaseException:
    print('EXC')
try:
    pairs
except BaseException:
    print('EXC')
try:
    print(my_function.__doc__)
except BaseException:
    print('EXC')
try:
    f('spam')
except BaseException:
    print('EXC')
