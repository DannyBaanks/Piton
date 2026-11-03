caught = 0
try:
    x = 32 + None
except TypeError:
    caught += 1
try:
    y = None - 32
except TypeError:
    caught += 1
try:
    z = None * 2
except TypeError:
    caught += 1
try:
    w = 10 // None
except TypeError:
    caught += 1
print('typeerror_none_caught', caught)

