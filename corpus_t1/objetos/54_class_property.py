class Temperature:
    def __init__(self, celsius):
        self.celsius = celsius
    @property
    def fahrenheit(self):
        return self.celsius * 9 / 5 + 32
print(Temperature(20).fahrenheit)
