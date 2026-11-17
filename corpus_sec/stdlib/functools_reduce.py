from functools import reduce
import operator
result = reduce(operator.add, [1,2,3,4])
print(result)
