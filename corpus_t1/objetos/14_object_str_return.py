class Label:
    def __init__(self, value):
        self.value = value
    def text(self):
        return str(self.value)
print(Label(42).text())
