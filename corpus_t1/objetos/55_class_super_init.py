class Animal:
    def __init__(self, name):
        self.name = name
class Dog(Animal):
    def __init__(self, name):
        super().__init__(name)
print(Dog("Rex").name)
