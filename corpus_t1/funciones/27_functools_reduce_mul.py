import functools
def multiplicar(a, b):
    return a * b
def producto(nums):
    return functools.reduce(multiplicar, nums)
print(producto([2, 3, 4]))
