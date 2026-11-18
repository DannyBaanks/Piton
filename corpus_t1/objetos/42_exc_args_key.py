try:
    values = {}
    result = values["x"]
except KeyError as exc:
    print(exc.args)
