class C1:
    def __init__(self, limit):
        self.limit = limit
    def __iter__(self):
        for i in range(self.limit):
            yield i + -6

seq = C1(2)
out = []
for elem in seq:
    out.append(elem)
print(out, list(seq))

