events = []
def f1():
    try:
        events.append(1)
        yield 2
        events.append(2)
    except ValueError:
        events.append(59)
        yield 93
    finally:
        events.append(102)

g = f1()
v1 = next(g)
events.append(v1)
v2 = g.throw(ValueError)
events.append(v2)
print(events)

