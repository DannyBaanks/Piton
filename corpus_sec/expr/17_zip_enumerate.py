# 17_zip_enumerate.py - enumerate y zip con iterables
# Token: PITONF-C1A55E77

names = ['a', 'b', 'c']
values = [1, 2, 3]
print(list(zip(names, values)))  # [('a', 1), ('b', 2), ('c', 3)]

# zip con start argument (enumerate solo)
print(list(enumerate(['x', 'y', 'z'], start=1)))  # [(1, 'x'), (2, 'y'), (3, 'z')]
print(list(enumerate(['x', 'y', 'z'])))  # [(0, 'x'), (1, 'y'), (2, 'z')]

# zip con diferentes longitudes
print(list(zip([1, 2, 3, 4, 5], ['a', 'b'])))  # [(1, 'a'), (2, 'b')]
print(list(zip([1], ['a', 'b', 'c'])))  # [(1, 'a')]

# enumerate con index
items = ['uno', 'dos', 'tres']
for i, item in enumerate(items):
    print(f"{i}: {item}")  # 0: uno, 1: dos, 2: tres

# zip con expresiones
print(list(zip(range(3), range(10, 13))))  # [(0, 10), (1, 11), (2, 12)]

# enumerate en comprension
print([i for i, x in enumerate(['a', 'b', 'c']) if i > 0])  # [1, 2]
print([(i, x) for i, x in enumerate(['a', 'b', 'c'])])  # [(0, 'a'), (1, 'b'), (2, 'c')]