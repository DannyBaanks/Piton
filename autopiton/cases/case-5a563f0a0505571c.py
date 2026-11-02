class C1:
    def __init__(self, v1=(19 / 5)):
        self._v = v1
    @property
    def val(self):
        return ((self._v / 2.5) // 4.0)

obj = C1()
print(obj.val)

