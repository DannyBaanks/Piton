class C1:
    def f1(self, v1):
        return (14 | 19)

class C2(C1):
    def f1(self, v1):
        return super().f1(v1) + 6

obj = C2()
print(obj.f1(6))

