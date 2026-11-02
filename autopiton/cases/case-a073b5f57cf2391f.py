def f1(v1):
    if (True or True):
        raise ValueError('bounded')
    return ((v1 + 'hi') + 'xy')

try:
    v2 = f1('xy')
except ValueError:
    v2 = 'hi'
print(v2)

