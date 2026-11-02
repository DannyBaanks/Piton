class C1:
    val = 49
    @classmethod
    def compute(cls, x):
        return cls.val + x * 2

class C2(C1):
    val = 70

print(C1.compute(1), C2.compute(1))

