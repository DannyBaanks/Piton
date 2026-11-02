def f1(v1=('ab' + 'xy')):
    v2 = 'hi'
    def f2():
        return (('ok' * 3) + 'ok')
    return f2()

print(f1())

