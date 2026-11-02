class C1:
    def __init__(self, target):
        self.target = target
    def __contains__(self, item):
        return item == self.target

box = C1(12)
print(12 in box, 19 in box, 19 not in box)

