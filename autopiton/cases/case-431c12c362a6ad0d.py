class C1:
    def __init__(self, v1=('hi' * 2)):
        self._v = v1
    @property
    def val(self):
        return ('cd' * 1)

obj = C1()
print(obj.val)

