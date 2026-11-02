class C1:
    def __init__(self):
        self._d = {'k1': (-3 ^ 30)}
    def __getitem__(self, key):
        return self._d[key]
    def __setitem__(self, key, val):
        self._d[key] = val
    def __len__(self):
        return len(self._d)

inst = C1()
v_read = inst['k1']
inst['k2'] = (-1 - 7)
inst['k3'] = 12
print(v_read, inst['k2'], inst['k3'], len(inst))

