class C1:
    def __init__(self, v1=(False and True)):
        self._v = v1
    @property
    def val(self):
        return True

obj = C1()
print(obj.val)

