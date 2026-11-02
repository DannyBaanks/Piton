def f1(v1=(5 & 25)):
    v2 = 6
    def f2():
        return ((1 << 0) - (~(v1)))
    return f2()

print(f1())

