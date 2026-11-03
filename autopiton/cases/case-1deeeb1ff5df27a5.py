def f1(v1):
    return v1

vals = [f1(i) for i in range(2) if (False or True)]
print(vals)

