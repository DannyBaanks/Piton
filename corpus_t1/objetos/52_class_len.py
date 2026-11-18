class Bag:
    def __init__(self, items):
        self.items = items
    def __len__(self):
        return len(self.items)
print(len(Bag([1, 2, 3])))
