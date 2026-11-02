class C1:
    def __init__(self, v1=False):
        self._v = v1
    @property
    def val(self):
        return ((self._v and self._v) and (5 < 6))

obj = C1()
print(obj.val)

