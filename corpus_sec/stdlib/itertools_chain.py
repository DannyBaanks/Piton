import itertools
a = [1,2]
b = [3,4]
c = [5]
result = list(itertools.chain(a,b,c))
print(result)
