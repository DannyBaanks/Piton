def f1(v1=('xy' != 'hi')):
    v2 = True
    def f2():
        return (('hi' + 'pq') > ('xy' + 'cd'))
    return f2()

print(f1())

