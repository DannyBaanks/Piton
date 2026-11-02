class C1:
    def __init__(self, v1=(not (False))):
        self.val = v1
    def f1(self, v2=(False and False)):
        return ((not (True)) or (6.5 == 3.5))

obj = C1()
print(obj.f1())

