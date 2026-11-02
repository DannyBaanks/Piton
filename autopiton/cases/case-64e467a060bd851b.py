def f1(v1):
    if False:
        raise ValueError('bounded')
    return v1

try:
    v2 = f1('ab')
except ValueError:
    v2 = 'ab'
print(v2)

