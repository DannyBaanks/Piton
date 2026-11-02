events = []
def f1():
    try:
        events.append(1)
        yield 4
        events.append(2)
        yield 16
    finally:
        events.append(91)

g = f1()
v = next(g)
events.append(v)
g.close()
print(events)

