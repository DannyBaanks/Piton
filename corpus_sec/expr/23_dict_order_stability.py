# 23_dict_order_stability.py - estabilidad de orden en dict
# Token: PITONF-C1A55E77

# dict mantiene orden de insercion (Python 3.7+)
d = {}
d["c"] = 3
d["a"] = 1
d["b"] = 2
print(list(d.keys()))  # ['c', 'a', 'b']
print(list(d.values()))  # [3, 1, 2]
print(list(d.items()))  # [('c', 3), ('a', 1), ('b', 2)]

# ordenamiento estable con key (sin lambda)
items = [(3, 'c'), (1, 'a'), (2, 'b'), (1, 'b')]
print(sorted(items))  # [(1, 'a'), (1, 'b'), (2, 'b'), (3, 'c')]

# sorted mantiene estabilidad
words = ['apple', 'bat', 'ant', 'ball', 'cat']
print(sorted(words))  # ['ant', 'apple', 'bat', 'ball', 'cat']
print(sorted(words, key=len))  # ['bat', 'cat', 'ant', 'ball', 'apple']