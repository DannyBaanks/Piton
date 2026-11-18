import json
def leer(texto):
    return json.loads(texto)
print(leer('{"a": 1, "b": 2}'))
