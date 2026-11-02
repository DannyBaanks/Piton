v1 = []
for i in range(4):
    try:
        v1.append(i)
        if i == 3:
            break
    finally:
        v1.append(100 + i)

print(v1)

