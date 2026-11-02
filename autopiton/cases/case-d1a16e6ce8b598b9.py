def f1(v1=(True or True)):
    v2 = False
    def f2():
        return ((True or v1) and (0 > -1))
    return f2()

print(f1())

