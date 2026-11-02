class C1:
    def __init__(self, v1=(-(3.5))):
        self._v = v1
    @property
    def val(self):
        return (22 / 4)

obj = C1()
print(obj.val)

