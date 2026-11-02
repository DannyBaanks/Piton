def f1(v1=('ab' == 'xy')):
    v2 = False
    def f2():
        return ((v2 or v1) or ('xy' != 'xy'))
    return f2()

print(f1())

