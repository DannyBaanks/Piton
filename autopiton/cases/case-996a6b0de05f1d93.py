def f1(v1):
    if (not (False)):
        raise ValueError('bounded')
    return ((v1 + 'xy') * 2)

try:
    v2 = f1('hi')
except ValueError:
    v2 = 'hi'
print(v2)

