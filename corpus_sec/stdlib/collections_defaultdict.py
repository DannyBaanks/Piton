from collections import defaultdict
d = defaultdict(list)
for k in [1,1,2]:
    d[k].append(k*10)
print(dict(d))
