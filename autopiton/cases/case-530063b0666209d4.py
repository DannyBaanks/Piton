def f1(v1='cd'):
    v2 = 'xy'
    def f2():
        return ((v1 * 2) * 2)
    return f2()

print(f1())

