class A:
    def f(self, *args):
        return len(args)

class B(A):
    def g(self):
        return super().f(1, 2, 3, 4)

print(B().g())
