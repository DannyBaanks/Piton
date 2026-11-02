class C1:
    val = 17
    @classmethod
    def compute(cls, x):
        return cls.val + x * 4

class C2(C1):
    val = 69

print(C1.compute(1), C2.compute(1))

