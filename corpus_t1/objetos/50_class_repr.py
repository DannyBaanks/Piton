class Item:
    def __init__(self, name):
        self.name = name
    def __repr__(self):
        return f"Item({self.name!r})"
print(repr(Item("a")))
