class C1:
    def __init__(self, v1='pq'):
        self.val = v1
    def f1(self, v2='hi'):
        return ((self.val + 'ab') + 'hi')

obj = C1()
print(obj.f1())

