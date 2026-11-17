import tempfile, os
tmpdir = tempfile.mkdtemp()
path = os.path.join(tmpdir,'acc.txt')
text = 'áéíóú'
with open(path,'w',encoding='utf-8') as f:
    f.write(text)
with open(path,'r',encoding='utf-8') as f:
    out = f.read()
print(out)
