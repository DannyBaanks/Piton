class C1:
    def __init__(self, limit):
        self.limit = limit
    def __iter__(self):
        for i in range(self.limit):
            yield i + -5

seq = C1(3)
out = []
for elem in seq:
    out.append(elem)
print(out, list(seq))

