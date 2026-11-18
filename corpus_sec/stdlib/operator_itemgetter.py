from operator import itemgetter
data = [('a',1),('b',2)]
getter = itemgetter(1)
result = list(map(getter, data))
print(result)
