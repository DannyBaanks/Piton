def f1(v1=-3):
    v2 = 6
    def f2():
        return ((v1 & 20) // 4)
    return f2()

print(f1())

