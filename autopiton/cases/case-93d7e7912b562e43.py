def f1(v1=(-2 - 6)):
    v2 = -6
    def f2():
        return (v2 & 13)
    return f2()

print(f1())

