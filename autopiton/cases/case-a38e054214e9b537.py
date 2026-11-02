def f1(v1=(False and True)):
    v2 = True
    def f2():
        return True
    return f2()

print(f1())

