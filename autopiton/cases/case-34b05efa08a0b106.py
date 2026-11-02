class C1:
    def __init__(self, v1=('ok' + 'ok')):
        self.val = v1
    def f1(self, v2=('ok' + 'xy')):
        return (('ab' + 'hi') + 'ab')

obj = C1()
print(obj.f1())

