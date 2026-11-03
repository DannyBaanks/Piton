text = '   cherry   fox  \t  piton  \n  '
p1 = text.split()
p2 = '1:2:3:4:5'.split(':', 2)
multi = 'line1\nline2\nline3'
l_keep = multi.splitlines(True)
l_drop = multi.splitlines(False)
print(p1, p2, l_keep, l_drop)

