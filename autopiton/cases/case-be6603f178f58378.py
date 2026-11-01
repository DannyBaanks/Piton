class _C:
    def _m(self):
        return 'ab' + 'cd'
_b = _C()._m()
_v = lambda: _b
print(_v())

