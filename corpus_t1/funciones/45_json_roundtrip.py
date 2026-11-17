import json
def ida_y_vuelta(dato):
    return json.loads(json.dumps(dato))
print(ida_y_vuelta({"k": [1, 2]}))
