class C1:
    def __init__(self, v1=(~(1))):
        self._v = v1
    @property
    def val(self):
        return ((0 | 12) % 7)

obj = C1()
print(obj.val)

