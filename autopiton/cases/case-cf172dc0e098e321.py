s1 = '{1} {0} {1}'.format('A', 'B')
s2 = '{!r} {!s}'.format('gamma', 75)
s3 = 'item={name!r} val={val}'.format(name='gamma', val=75)
print(s1, s2, s3)

