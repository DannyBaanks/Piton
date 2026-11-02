def f1(v1):
    for i in range(v1):
        yield i * 2
    return v1 + 46

def f2(n):
    v2 = yield from f1(n)
    yield v2
    yield from [99, 100]

print(list(f2(3)))

