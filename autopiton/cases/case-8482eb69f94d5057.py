class C1:
    val = 27
    @classmethod
    def compute(cls, x):
        return cls.val + x * 3

class C2(C1):
    val = 75

print(C1.compute(1), C2.compute(1))

