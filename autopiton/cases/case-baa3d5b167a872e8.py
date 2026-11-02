class C1:
    def f1(self, v1):
        return (v1 * 2)

class C2(C1):
    def f1(self, v1):
        return super().f1(v1) + 'pq'

obj = C2()
print(obj.f1('hi'))

