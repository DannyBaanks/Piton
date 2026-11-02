class C1:
    def __init__(self, v1=(False == False)):
        self._v = v1
    @property
    def val(self):
        return False

obj = C1()
print(obj.val)

