values = {}
try:
    result = values["missing"]
except KeyError as exc:
    print(type(exc).__name__, exc.args)
