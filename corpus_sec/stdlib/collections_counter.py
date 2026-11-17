from collections import Counter
data = [1,2,2,3,3,3]
c = Counter(data)
most = c.most_common()
print(most)
