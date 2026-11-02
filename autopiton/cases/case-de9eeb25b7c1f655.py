class C1:
    val = 19
    @classmethod
    def compute(cls, x):
        return cls.val + x * 5

class C2(C1):
    val = 98

print(C1.compute(1), C2.compute(1))

