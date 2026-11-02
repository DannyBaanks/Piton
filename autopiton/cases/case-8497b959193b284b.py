s1 = '{1} {0} {1}'.format('A', 'B')
s2 = '{!r} {!s}'.format('gamma', 56)
s3 = 'item={name!r} val={val}'.format(name='gamma', val=56)
print(s1, s2, s3)

