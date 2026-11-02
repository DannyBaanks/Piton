class C1:
    def __init__(self, v1=(-3 >= -8)):
        self._v = v1
    @property
    def val(self):
        return ((not (self._v)) and (10 > -5))

obj = C1()
print(obj.val)

