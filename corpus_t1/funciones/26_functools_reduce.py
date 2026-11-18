import functools
def sumar(a, b):
    return a + b
def total(nums):
    return functools.reduce(sumar, nums)
print(total([1, 2, 3, 4]))
