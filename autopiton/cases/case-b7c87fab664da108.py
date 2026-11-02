def f1(v1):
    return (9 | 24)

vals = [f1(i) for i in range(2) if (False or True)]
print(vals)

