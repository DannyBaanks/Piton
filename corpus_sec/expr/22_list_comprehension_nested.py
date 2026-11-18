# 22_list_comprehension_nested.py - comprensiones anidadas con filtros
# Token: PITONF-C1A55E77

# comprension anidada con filtro
print([[x, y] for x in [1, 2] for y in [3, 4] if x != y])  # [[1, 3], [1, 4], [2, 3], [2, 4]]

# filtro en la primera capa
print([[x, y] for x in [1, 2, 3] if x > 1 for y in [10, 20]])  # [[2, 10], [2, 20], [3, 10], [3, 20]]

# filtro en ambas capas
print([[x, y] for x in [1, 2, 3] if x > 1 for y in [10, 20] if y > 15])  # [[2, 20], [3, 20]]

# comprension de diccionario anidada
print({x: {y: x + y for y in [1, 2]} for x in [10, 20]})  # {10: {1: 11, 2: 12}, 20: {1: 21, 2: 22}}

# lista de diccionarios con filtro (sin generator)
print([{'x': x, 'y': x * 2} for x in range(3) if x > 0])  # [{'x': 1, 'y': 2}, {'x': 2, 'y': 4}]
print([{x: x * 2} for x in range(3) if x > 0])  # [{'1': 2}, {'2': 4}, {'3': 6}]

# comprehension con múltiples condiciones
print([x for x in range(20) if x % 2 == 0 if x > 10])  # [12, 14, 16, 18]