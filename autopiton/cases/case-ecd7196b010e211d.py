def f1():
    v1 = 18
    def f2(v2):
        nonlocal v1
        v1 *= v2
        return v1
    out = []
    for i in range(3):
        out.append(f2(i + 1))
    return out, v1

print(f1())

