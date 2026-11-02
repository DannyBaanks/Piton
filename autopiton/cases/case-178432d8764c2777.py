class C1:
    def __init__(self, v1='ab'):
        self.val = v1
    def f1(self, v2='pq'):
        return (('ok' * 3) + 'ab')

obj = C1()
print(obj.f1())

