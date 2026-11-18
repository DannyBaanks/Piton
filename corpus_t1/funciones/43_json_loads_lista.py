import json
def leer(texto):
    return json.loads(texto)
print(leer("[1, 2, 3]"))
