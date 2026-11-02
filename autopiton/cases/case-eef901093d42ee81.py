class C1:
    def __init__(self, p, tag):
        self.p = p
        self.tag = tag
    def __eq__(self, other):
        return self.p == other.p
    def __lt__(self, other):
        return self.p < other.p
    def __le__(self, other):
        return self.p <= other.p

x = C1(-6, 'x')
y = C1(6, 'y')
print(x == y, x != y, x < y, x <= y)

