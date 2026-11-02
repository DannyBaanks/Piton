class C1:
    def f1(self, v1):
        return ('pq' * 1)

class C2(C1):
    def f1(self, v1):
        return super().f1(v1) + 'cd'

obj = C2()
print(obj.f1('xy'))

