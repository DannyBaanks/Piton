class Counter:
    def __init__(self, limit):
        self.limit = limit
    def __iter__(self):
        return iter(range(self.limit))
print(list(Counter(4)))
