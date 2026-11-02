def f1(v1=('ab' + 'cd')):
    v2 = 'cd'
    def f2():
        return (('cd' * 1) * 2)
    return f2()

print(f1())

