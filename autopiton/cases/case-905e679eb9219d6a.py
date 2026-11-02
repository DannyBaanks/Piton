class C1:
    def __init__(self, target):
        self.target = target
    def __contains__(self, item):
        return item == self.target

box = C1(-8)
print(-8 in box, -1 in box, -1 not in box)

