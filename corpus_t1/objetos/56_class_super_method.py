class Animal:
    def sound(self):
        return "..."
class Dog(Animal):
    def sound(self):
        return super().sound() + "woof"
print(Dog().sound())
