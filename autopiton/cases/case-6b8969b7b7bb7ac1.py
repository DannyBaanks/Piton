class C1:
    val = 11
    @classmethod
    def compute(cls, x):
        return cls.val + x * 3

class C2(C1):
    val = 65

print(C1.compute(1), C2.compute(1))

