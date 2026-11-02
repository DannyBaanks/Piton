class C1:
    def __init__(self, v1='hi'):
        self._v = v1
    @property
    def val(self):
        return (self._v * 1)

obj = C1()
print(obj.val)

