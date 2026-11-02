v1 = []
for i in range(2):
    try:
        v1.append(i)
        if i == 1:
            break
    finally:
        v1.append(100 + i)

print(v1)

