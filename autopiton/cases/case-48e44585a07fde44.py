class C1:
    @staticmethod
    def calc(a, b):
        return a + b + 13

class C2(C1):
    pass

inst = C2()
print(C1.calc(2, 3), C2.calc(4, 5), inst.calc(6, 7))

