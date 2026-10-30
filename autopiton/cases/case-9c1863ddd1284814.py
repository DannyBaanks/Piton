def decorator(f):
    return f

@decorator
def func():
    return 42

print(func())
