s1 = '{1} {0} {1}'.format('A', 'B')
s2 = '{!r} {!s}'.format('beta', 91)
s3 = 'item={name!r} val={val}'.format(name='beta', val=91)
print(s1, s2, s3)

