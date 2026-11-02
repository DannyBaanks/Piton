m1 = min(True, 6, 0)
m2 = max(False, -5, 13)
words = ['banana', 'apple', 'fig', 'strawberry']
m_word = min(words, key=len)
nums = [-15, 3, -2, 8]
m_num = max(nums, key=abs)
print(m1, m2, m_word, m_num)

