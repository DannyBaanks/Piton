from collections import deque
dq = deque([1,2,3])
dq.appendleft(0)
dq.rotate(1)
print(list(dq))
