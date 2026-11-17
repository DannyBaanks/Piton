import json
d = {'a':1,'b':[2,3],'c':{'x': True}}
s = json.dumps(d, sort_keys=True, indent=2)
obj = json.loads(s)
print(s)
print(obj)
