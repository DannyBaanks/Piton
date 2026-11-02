class C1:
    def __init__(self, target):
        self.target = target
    def __contains__(self, item):
        return item == self.target

box = C1(5)
print(5 in box, 12 in box, 12 not in box)

