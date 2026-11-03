s1 = '{1} {0} {1}'.format('A', 'B')
s2 = '{!r} {!s}'.format('alpha', 39)
s3 = 'item={name!r} val={val}'.format(name='alpha', val=39)
print(s1, s2, s3)

