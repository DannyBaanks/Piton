def f(*args):
    return sum(args)

print(f(*[1, 2], *[3, 4]))
