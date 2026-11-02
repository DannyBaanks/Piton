def f1(v1):
    return (v1 | 11)

vals = [f1(i) for i in range(4) if False]
print(vals)

