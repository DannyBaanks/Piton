def gen():
    yield 1
    yield 2
    yield 3

print(list(gen()))
