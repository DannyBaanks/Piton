s1 = '{1} {0} {1}'.format('A', 'B')
s2 = '{!r} {!s}'.format('beta', 98)
s3 = 'item={name!r} val={val}'.format(name='beta', val=98)
print(s1, s2, s3)

