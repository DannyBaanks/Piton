import json
def volcar(dato):
    return json.dumps(dato)
print(volcar({"b": 2, "a": 1}))
