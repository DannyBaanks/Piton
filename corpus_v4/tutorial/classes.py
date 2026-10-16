# corpus_v4 tutorial page: classes
# source: https://docs.python.org/3.12/tutorial/classes.html
# sha256(HTML): 3e82b879f4d08cc226bb034986369da5192a24d65ee58734aa330a823f2b711a
# ejemplos doctest: 48 (excluidos 4 por sintaxis rota intencional)
# convertido por tools/parity_corpus_v4.py fetch: cada ejemplo queda en
# try/except BaseException (semantica REPL: la sesion no muere en un error)
try:
    x = Complex(3.0, -4.5)
except BaseException:
    print('EXC')
try:
    x.r, x.i
except BaseException:
    print('EXC')
try:
    d = Dog('Fido')
except BaseException:
    print('EXC')
try:
    e = Dog('Buddy')
except BaseException:
    print('EXC')
try:
    d.kind                  # shared by all dogs
except BaseException:
    print('EXC')
try:
    e.kind                  # shared by all dogs
except BaseException:
    print('EXC')
try:
    d.name                  # unique to d
except BaseException:
    print('EXC')
try:
    e.name                  # unique to e
except BaseException:
    print('EXC')
try:
    d = Dog('Fido')
except BaseException:
    print('EXC')
try:
    e = Dog('Buddy')
except BaseException:
    print('EXC')
try:
    d.add_trick('roll over')
except BaseException:
    print('EXC')
try:
    e.add_trick('play dead')
except BaseException:
    print('EXC')
try:
    d.tricks                # unexpectedly shared by all dogs
except BaseException:
    print('EXC')
try:
    d = Dog('Fido')
except BaseException:
    print('EXC')
try:
    e = Dog('Buddy')
except BaseException:
    print('EXC')
try:
    d.add_trick('roll over')
except BaseException:
    print('EXC')
try:
    e.add_trick('play dead')
except BaseException:
    print('EXC')
try:
    d.tricks
except BaseException:
    print('EXC')
try:
    e.tricks
except BaseException:
    print('EXC')
try:
    w1 = Warehouse()
except BaseException:
    print('EXC')
try:
    print(w1.purpose, w1.region)
except BaseException:
    print('EXC')
try:
    w2 = Warehouse()
except BaseException:
    print('EXC')
try:
    w2.region = 'east'
except BaseException:
    print('EXC')
try:
    print(w2.purpose, w2.region)
except BaseException:
    print('EXC')
try:
    john = Employee('john', 'computer lab', 1000)
except BaseException:
    print('EXC')
try:
    john.dept
except BaseException:
    print('EXC')
try:
    john.salary
except BaseException:
    print('EXC')
try:
    s = 'abc'
except BaseException:
    print('EXC')
try:
    it = iter(s)
except BaseException:
    print('EXC')
try:
    it
except BaseException:
    print('EXC')
try:
    next(it)
except BaseException:
    print('EXC')
try:
    next(it)
except BaseException:
    print('EXC')
try:
    next(it)
except BaseException:
    print('EXC')
try:
    next(it)
except BaseException:
    print('EXC')
try:
    rev = Reverse('spam')
except BaseException:
    print('EXC')
try:
    iter(rev)
except BaseException:
    print('EXC')
try:
    sum(i*i for i in range(10))                 # sum of squares
except BaseException:
    print('EXC')
try:
    xvec = [10, 20, 30]
except BaseException:
    print('EXC')
try:
    yvec = [7, 5, 3]
except BaseException:
    print('EXC')
try:
    sum(x*y for x,y in zip(xvec, yvec))         # dot product
except BaseException:
    print('EXC')
try:
    unique_words = set(word for line in page  for word in line.split())
except BaseException:
    print('EXC')
try:
    valedictorian = max((student.gpa, student.name) for student in graduates)
except BaseException:
    print('EXC')
try:
    data = 'golf'
except BaseException:
    print('EXC')
try:
    list(data[i] for i in range(len(data)-1, -1, -1))
except BaseException:
    print('EXC')
