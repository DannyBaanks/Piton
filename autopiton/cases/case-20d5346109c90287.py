def f1(v1):
    if (not (True)):
        raise ValueError('bounded')
    return 'xy'

try:
    v2 = f1('hi')
except ValueError:
    v2 = 'pq'
print(v2)

