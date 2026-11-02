def f1(v1=11):
    v2 = -3
    def f2():
        return ((2 << 0) * (v1 | 2))
    return f2()

print(f1())

