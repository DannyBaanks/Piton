class C1:
    def __init__(self, target):
        self.target = target
    def __contains__(self, item):
        return item == self.target

box = C1(9)
print(9 in box, 16 in box, 16 not in box)

