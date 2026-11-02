class C1:
    def __init__(self, target):
        self.target = target
    def __contains__(self, item):
        return item == self.target

box = C1(11)
print(11 in box, 18 in box, 18 not in box)

