class C1:
    val = 10
    @classmethod
    def compute(cls, x):
        return cls.val + x * 2

class C2(C1):
    val = 88

print(C1.compute(1), C2.compute(1))

