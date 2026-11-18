import itertools
data = [1,1,2,2,2,3,1]
# groupby requires sorted for consecutive groups
data_sorted = sorted(data)
groups = []
for key, group in itertools.groupby(data_sorted):
    groups.append((key, list(group)))
print(groups)
