def f1(v1=(not (True))):
    v2 = False
    def f2():
        return ((2.25 != 4.75) or (not (True)))
    return f2()

print(f1())

