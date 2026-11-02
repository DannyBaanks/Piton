class C1:
    def __init__(self, v1='xy'):
        self.val = v1
    def f1(self, v2=('ab' * 3)):
        return (('pq' * 3) + 'ab')

obj = C1()
print(obj.f1())

