i_zero = int()
i_hex = int('0xe8', 16)
i_oct = int('0o350', 8)
i_bin = int('0b11101000', 2)
i_raw_hex = int('e8', 16)
i_base0_hex = int('0xe8', 0)
i_base0_oct = int('0o350', 0)
i_base0_bin = int('0b11101000', 0)
i_base36 = int('p', 36)
err_caught = 0
try:
    int('bad_digit', 10)
except ValueError:
    err_caught += 1
try:
    int(123, 10)
except TypeError:
    err_caught += 1
print(i_zero, i_hex, i_oct, i_bin, i_raw_hex, i_base0_hex, i_base0_oct, i_base0_bin, i_base36, err_caught)

