import json
def volcar(dato):
    return json.dumps(dato, sort_keys=True)
print(volcar({"b": 2, "a": 1}))
