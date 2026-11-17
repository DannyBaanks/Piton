# 14_list_sort_key.py - ordenacion con key, estabilidad
# Token: PITONF-C1A55E77

print(sorted([3, 1, 4, 1, 5, 9, 2, 6]))  # [1, 1, 2, 3, 4, 5, 6, 9]
print(sorted([3, 1, 4, 1, 5, 9, 2, 6], reverse=True))  # [9, 6, 5, 4, 3, 2, 1, 1]

l = [3, 1, 4, 1, 5, 9, 2, 6]
l.sort()
print(l)  # [1, 1, 2, 3, 4, 5, 6, 9]

words = ['banana', 'pie', 'apple', 'cherry']
print(sorted(words))  # ['apple', 'banana', 'cherry', 'pie']
print(sorted(words, key=len))  # ['pie', 'apple', 'banana', 'cherry']

pairs = [(1, 'a'), (3, 'c'), (2, 'b'), (1, 'b')]
print(sorted(pairs))  # [(1, 'a'), (1, 'b'), (2, 'b'), (3, 'c')]

data = [5, 2, 2, 3, 1, 2]
print(sorted(data))  # [1, 2, 2, 2, 3, 5]