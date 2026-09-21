def g():
    yield 0
    yield 1
    yield 2

x = g()
print(next(x))
print(x.send(5))
print(x.send(7))
