s1 = '{1} {0} {1}'.format('A', 'B')
s2 = '{!r} {!s}'.format('alpha', 42)
s3 = 'item={name!r} val={val}'.format(name='alpha', val=42)
print(s1, s2, s3)

