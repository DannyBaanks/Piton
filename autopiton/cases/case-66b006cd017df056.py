def f1(v1=False):
    v2 = True
    def f2():
        return (True or (True or v2))
    return f2()

print(f1())

