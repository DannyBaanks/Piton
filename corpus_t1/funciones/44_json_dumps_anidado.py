import json
def volcar(dato):
    return json.dumps(dato)
print(volcar({"x": {"y": [1, 2]}}))
