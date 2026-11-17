values = []
try:
    result = values.pop()
except IndexError as exc:
    print(type(exc).__name__, exc.args)
