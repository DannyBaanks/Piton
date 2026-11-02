raw = [0, 3, False, True, '', 'ok', [], [20]]
f_none = list(filter(None, raw))
nums = [-5, -2, 0, 3, 7]
f_pos = list(filter(lambda x: x > 0, nums))
f_bool = tuple(filter(bool, [0, 1, 2, 0, 4]))
print(f_none, f_pos, f_bool)

