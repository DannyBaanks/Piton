def f1(v1):
    if (True or True):
        raise ValueError('bounded')
    return ((v1 + 'ok') + 'pq')

try:
    v2 = f1('pq')
except ValueError:
    v2 = 'ab'
print(v2)

