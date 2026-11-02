class C1:
    def __init__(self, v1=(not (True))):
        self._v = v1
    @property
    def val(self):
        return (not (self._v))

obj = C1()
print(obj.val)

