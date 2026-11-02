def f1(extra):
    class C1:
        base_extra = extra
        def __init__(self, val):
            self.val = val * 4 + self.base_extra + 13
        def get(self):
            return self.val
    return C1

ClsA = f1(10)
ClsB = f1(20)
a = ClsA(3)
b = ClsB(3)
print(a.get(), b.get())

