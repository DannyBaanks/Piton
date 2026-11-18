import tempfile, os
tmpdir = tempfile.mkdtemp()
path = os.path.join(tmpdir, 'tmp.txt')
with open(path,'w',encoding='utf-8') as f:
    f.write('hello\nworld')
with open(path,'r',encoding='utf-8') as f:
    content = f.read()
print(content)
