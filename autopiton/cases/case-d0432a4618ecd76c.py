class C1:
    def __init__(self, v1=(10 + -5)):
        self.val = v1
    def f1(self, v2=(-(1))):
        return (-((self.val + v2)))

obj = C1()
print(obj.f1())

