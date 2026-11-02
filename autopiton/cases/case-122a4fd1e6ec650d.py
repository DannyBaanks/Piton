def f1(v1):
    return (v1 & 31)

vals = [f1(i) for i in range(2) if True]
print(vals)

