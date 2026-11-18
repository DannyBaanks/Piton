class Builder:
    def __init__(self, parts):
        self.parts = parts
    def render(self):
        return "|".join(self.parts)
print(Builder(["a", "b"]).render())
