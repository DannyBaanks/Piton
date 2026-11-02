def f1(v1=(not (True))):
    v2 = True
    def f2():
        return (True != (v1 or True))
    return f2()

print(f1())

