class Box:
    def __init__(self, value):
        self.value = value
    def __eq__(self, other):
        return self.value == other.value
print(Box(2) == Box(2))
