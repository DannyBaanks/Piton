# corpus_v4 tutorial page: datastructures
# source: https://docs.python.org/3.12/tutorial/datastructures.html
# sha256(HTML): da391aa509c1609bf291925c9a96ac4e7edf2172632e9deb8e6e8b40010a1342
# ejemplos doctest: 135 (excluidos 23 por sintaxis rota intencional)
# convertido por tools/parity_corpus_v4.py fetch: cada ejemplo queda en
# try/except BaseException (semantica REPL: la sesion no muere en un error)
try:
    fruits = ['orange', 'apple', 'pear', 'banana', 'kiwi', 'apple', 'banana']
except BaseException:
    print('EXC')
try:
    fruits.count('apple')
except BaseException:
    print('EXC')
try:
    fruits.count('tangerine')
except BaseException:
    print('EXC')
try:
    fruits.index('banana')
except BaseException:
    print('EXC')
try:
    fruits.index('banana', 4)  # Find next banana starting at position 4
except BaseException:
    print('EXC')
try:
    fruits.reverse()
except BaseException:
    print('EXC')
try:
    fruits
except BaseException:
    print('EXC')
try:
    fruits.append('grape')
except BaseException:
    print('EXC')
try:
    fruits
except BaseException:
    print('EXC')
try:
    fruits.sort()
except BaseException:
    print('EXC')
try:
    fruits
except BaseException:
    print('EXC')
try:
    fruits.pop()
except BaseException:
    print('EXC')
try:
    stack = [3, 4, 5]
except BaseException:
    print('EXC')
try:
    stack.append(6)
except BaseException:
    print('EXC')
try:
    stack.append(7)
except BaseException:
    print('EXC')
try:
    stack
except BaseException:
    print('EXC')
try:
    stack.pop()
except BaseException:
    print('EXC')
try:
    stack
except BaseException:
    print('EXC')
try:
    stack.pop()
except BaseException:
    print('EXC')
try:
    stack.pop()
except BaseException:
    print('EXC')
try:
    stack
except BaseException:
    print('EXC')
try:
    from collections import deque
except BaseException:
    print('EXC')
try:
    queue = deque(["Eric", "John", "Michael"])
except BaseException:
    print('EXC')
try:
    queue.append("Terry")           # Terry arrives
except BaseException:
    print('EXC')
try:
    queue.append("Graham")          # Graham arrives
except BaseException:
    print('EXC')
try:
    queue.popleft()                 # The first to arrive now leaves
except BaseException:
    print('EXC')
try:
    queue.popleft()                 # The second to arrive now leaves
except BaseException:
    print('EXC')
try:
    queue                           # Remaining queue in order of arrival
except BaseException:
    print('EXC')
try:
    squares = []
except BaseException:
    print('EXC')
try:
    squares
except BaseException:
    print('EXC')
try:
    [(x, y) for x in [1,2,3] for y in [3,1,4] if x != y]
except BaseException:
    print('EXC')
try:
    combs = []
except BaseException:
    print('EXC')
try:
    combs
except BaseException:
    print('EXC')
try:
    vec = [-4, -2, 0, 2, 4]
except BaseException:
    print('EXC')
try:
    [x*2 for x in vec]
except BaseException:
    print('EXC')
try:
    [x for x in vec if x >= 0]
except BaseException:
    print('EXC')
try:
    [abs(x) for x in vec]
except BaseException:
    print('EXC')
try:
    freshfruit = ['  banana', '  loganberry ', 'passion fruit  ']
except BaseException:
    print('EXC')
try:
    [weapon.strip() for weapon in freshfruit]
except BaseException:
    print('EXC')
try:
    [(x, x**2) for x in range(6)]
except BaseException:
    print('EXC')
try:
    vec = [[1,2,3], [4,5,6], [7,8,9]]
except BaseException:
    print('EXC')
try:
    [num for elem in vec for num in elem]
except BaseException:
    print('EXC')
try:
    from math import pi
except BaseException:
    print('EXC')
try:
    [str(round(pi, i)) for i in range(1, 6)]
except BaseException:
    print('EXC')
try:
    matrix = [
    [1, 2, 3, 4],
    [5, 6, 7, 8],
    [9, 10, 11, 12],
    ]
except BaseException:
    print('EXC')
try:
    [[row[i] for row in matrix] for i in range(4)]
except BaseException:
    print('EXC')
try:
    transposed = []
except BaseException:
    print('EXC')
try:
    transposed
except BaseException:
    print('EXC')
try:
    transposed = []
except BaseException:
    print('EXC')
try:
    transposed
except BaseException:
    print('EXC')
try:
    list(zip(*matrix))
except BaseException:
    print('EXC')
try:
    a = [-1, 1, 66.25, 333, 333, 1234.5]
except BaseException:
    print('EXC')
try:
    del a[0]
except BaseException:
    print('EXC')
try:
    a
except BaseException:
    print('EXC')
try:
    del a[2:4]
except BaseException:
    print('EXC')
try:
    a
except BaseException:
    print('EXC')
try:
    del a[:]
except BaseException:
    print('EXC')
try:
    a
except BaseException:
    print('EXC')
try:
    del a
except BaseException:
    print('EXC')
try:
    t = 12345, 54321, 'hello!'
except BaseException:
    print('EXC')
try:
    t[0]
except BaseException:
    print('EXC')
try:
    t
except BaseException:
    print('EXC')
try:
    u = t, (1, 2, 3, 4, 5)
except BaseException:
    print('EXC')
try:
    u
except BaseException:
    print('EXC')
try:
    t[0] = 88888
except BaseException:
    print('EXC')
try:
    v = ([1, 2, 3], [3, 2, 1])
except BaseException:
    print('EXC')
try:
    v
except BaseException:
    print('EXC')
try:
    empty = ()
except BaseException:
    print('EXC')
try:
    singleton = 'hello',    # <-- note trailing comma
except BaseException:
    print('EXC')
try:
    len(empty)
except BaseException:
    print('EXC')
try:
    len(singleton)
except BaseException:
    print('EXC')
try:
    singleton
except BaseException:
    print('EXC')
try:
    x, y, z = t
except BaseException:
    print('EXC')
try:
    basket = {'apple', 'orange', 'apple', 'pear', 'orange', 'banana'}
except BaseException:
    print('EXC')
try:
    print(basket)                      # show that duplicates have been removed
except BaseException:
    print('EXC')
try:
    'orange' in basket                 # fast membership testing
except BaseException:
    print('EXC')
try:
    'crabgrass' in basket
except BaseException:
    print('EXC')
try:
    a = set('abracadabra')
except BaseException:
    print('EXC')
try:
    b = set('alacazam')
except BaseException:
    print('EXC')
try:
    a                                  # unique letters in a
except BaseException:
    print('EXC')
try:
    a - b                              # letters in a but not in b
except BaseException:
    print('EXC')
try:
    a | b                              # letters in a or b or both
except BaseException:
    print('EXC')
try:
    a & b                              # letters in both a and b
except BaseException:
    print('EXC')
try:
    a ^ b                              # letters in a or b but not both
except BaseException:
    print('EXC')
try:
    a = {x for x in 'abracadabra' if x not in 'abc'}
except BaseException:
    print('EXC')
try:
    a
except BaseException:
    print('EXC')
try:
    tel = {'jack': 4098, 'sape': 4139}
except BaseException:
    print('EXC')
try:
    tel['guido'] = 4127
except BaseException:
    print('EXC')
try:
    tel
except BaseException:
    print('EXC')
try:
    tel['jack']
except BaseException:
    print('EXC')
try:
    del tel['sape']
except BaseException:
    print('EXC')
try:
    tel['irv'] = 4127
except BaseException:
    print('EXC')
try:
    tel
except BaseException:
    print('EXC')
try:
    list(tel)
except BaseException:
    print('EXC')
try:
    sorted(tel)
except BaseException:
    print('EXC')
try:
    'guido' in tel
except BaseException:
    print('EXC')
try:
    'jack' not in tel
except BaseException:
    print('EXC')
try:
    dict([('sape', 4139), ('guido', 4127), ('jack', 4098)])
except BaseException:
    print('EXC')
try:
    {x: x**2 for x in (2, 4, 6)}
except BaseException:
    print('EXC')
try:
    dict(sape=4139, guido=4127, jack=4098)
except BaseException:
    print('EXC')
try:
    knights = {'gallahad': 'the pure', 'robin': 'the brave'}
except BaseException:
    print('EXC')
try:
    questions = ['name', 'quest', 'favorite color']
except BaseException:
    print('EXC')
try:
    answers = ['lancelot', 'the holy grail', 'blue']
except BaseException:
    print('EXC')
try:
    basket = ['apple', 'orange', 'apple', 'pear', 'orange', 'banana']
except BaseException:
    print('EXC')
try:
    basket = ['apple', 'orange', 'apple', 'pear', 'orange', 'banana']
except BaseException:
    print('EXC')
try:
    import math
except BaseException:
    print('EXC')
try:
    raw_data = [56.2, float('NaN'), 51.7, 55.3, 52.5, float('NaN'), 47.8]
except BaseException:
    print('EXC')
try:
    filtered_data = []
except BaseException:
    print('EXC')
try:
    filtered_data
except BaseException:
    print('EXC')
try:
    string1, string2, string3 = '', 'Trondheim', 'Hammer Dance'
except BaseException:
    print('EXC')
try:
    non_null = string1 or string2 or string3
except BaseException:
    print('EXC')
try:
    non_null
except BaseException:
    print('EXC')
