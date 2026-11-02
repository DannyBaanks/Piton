class C1:
    def __init__(self, v1=(10 | 27)):
        self.val = v1
    def f1(self, v2=(19 << 3)):
        return ((~(self.val)) + (8 | 10))

obj = C1()
print(obj.f1())

