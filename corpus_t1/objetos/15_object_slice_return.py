class Box:
    def __init__(self, values):
        self.values = values
    def middle(self):
        return self.values[1:3]
print(Box([0, 1, 2, 3]).middle())
