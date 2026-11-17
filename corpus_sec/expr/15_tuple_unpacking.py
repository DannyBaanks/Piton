# 15_tuple_unpacking.py - unpacking de tuplas
# Token: PITONF-C1A55E77

t = (1, 2, 3)
a, b, c = t
print(a)  # 1
print(b)  # 2
print(c)  # 3

# unpacking con asterisco
first, *middle, last = [1, 2, 3, 4, 5]
print(first)  # 1
print(middle)  # [2, 3, 4]
print(last)   # 5

# tuple return
def get_coords():
    return (10, 20)
x, y = get_coords()
print(x, y)  # 10 20

# nested unpacking
(a, b), (c, d) = (1, 2), (3, 4)
print(a, b, c, d)  # 1 2 3 4

# unpacking en bucle
for x, y in [(1, 2), (3, 4)]:
    print(x + y)  # 3, 7

# enumerate
print(list(enumerate(['a', 'b', 'c'])))  # [(0, 'a'), (1, 'b'), (2, 'c')]
for i, val in enumerate(['x', 'y', 'z']):
    print(i, val)  # 0 x, 1 y, 2 z

# zip
print(list(zip([1, 2, 3], ['a', 'b', 'c'])))  # [(1, 'a'), (2, 'b'), (3, 'c')]
for a, b in zip([1, 2, 3], ['x', 'y', 'z']):
    print(a, b)  # 1 x, 2 y, 3 z

# zip con listas de diferente longitud
print(list(zip([1, 2], [10, 20, 30, 40])))  # [(1, 10), (2, 20)]