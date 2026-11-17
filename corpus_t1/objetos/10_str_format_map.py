values = {"name": "Ada", "n": 3}
message = "{name}/{n}".format_map(values)
print(message)
