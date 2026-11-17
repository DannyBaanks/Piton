import itertools
# count from 5, take first 5
gen = itertools.count(5)
sliced = list(itertools.islice(gen, 5))
print(sliced)
