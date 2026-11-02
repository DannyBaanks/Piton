events = []
def f1():
    try:
        events.append(1)
        yield 2
        events.append(2)
        yield 17
    finally:
        events.append(96)

g = f1()
v = next(g)
events.append(v)
g.close()
print(events)

