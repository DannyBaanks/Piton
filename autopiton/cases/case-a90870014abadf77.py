class C1:
    def __init__(self, target):
        self.target = target
    def __contains__(self, item):
        return item == self.target

box = C1(10)
print(10 in box, 17 in box, 17 not in box)

