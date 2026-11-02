def f1(v1):
    return v1

vals = [f1(i) for i in range(4) if (True and False)]
print(vals)

