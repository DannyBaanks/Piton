class C1:
    def __init__(self, v1=(-(4.75))):
        self._v = v1
    @property
    def val(self):
        return self._v

obj = C1()
print(obj.val)

