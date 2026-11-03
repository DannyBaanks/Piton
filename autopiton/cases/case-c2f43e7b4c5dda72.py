def f1(v1=True):
    v2 = True
    def f2():
        return ((31 << 3) != (~(-6)))
    return f2()

print(f1())

