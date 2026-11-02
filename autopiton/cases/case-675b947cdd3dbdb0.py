class C1:
    def __init__(self, v1):
        self.v = v1
    def __len__(self):
        return abs(self.v)
    def __add__(self, v2):
        return self.v + v2

inst = C1((-6 | 20))
print(len(inst), inst + -3)

