events = []
def f1():
    try:
        events.append(1)
        yield 6
        events.append(2)
    except ValueError:
        events.append(64)
        yield 92
    finally:
        events.append(110)

g = f1()
v1 = next(g)
events.append(v1)
v2 = g.throw(ValueError)
events.append(v2)
print(events)

