import os, tempfile
tmpdir = tempfile.mkdtemp()
path = os.path.join(tmpdir, 'sub', 'file.txt')
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path,'w') as f:
    f.write('x')
basename = os.path.basename(path)
dirname = os.path.dirname(path)
exists = os.path.exists(path)
print(basename, dirname, exists)
