class C1:
    def __init__(self, v1=(not (False))):
        self.val = v1
    def f1(self, v2=(4.75 <= 2.25)):
        return ((True and v2) == (9.25 != 4.75))

obj = C1()
print(obj.f1())

