import math
import sys
pi_val = round(math.pi * 1, 4)
e_val = round(math.e, 4)
has_maxsize = sys.maxsize > 1000000
from math import pi as my_pi
from sys import maxsize as my_maxsize
match_pi = round(my_pi * 1, 4) == pi_val
match_max = my_maxsize == sys.maxsize
print(pi_val, e_val, has_maxsize, match_pi, match_max)

