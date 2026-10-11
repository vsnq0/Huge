from PyInstaller.archive.readers import ZlibArchiveReader
import marshal
import os
import time
import types

path = r'C:\Users\Admin\OneDrive\Desktop\Huge app\huge\build\Huge\PYZ-00.pyz'
src = r'C:\Users\Admin\OneDrive\Desktop\Huge app\huge\engine\session.py'
z = ZlibArchiveReader(path)
data = z.extract('engine.session')
code = data[0] if isinstance(data, tuple) else data
if isinstance(code, (bytes, bytearray)):
    code = marshal.loads(code)


def walk(c, depth=0):
    names = list(getattr(c, 'co_names', ()))
    hits = [n for n in names if 'broadcast_playing' in n]
    if hits:
        print(' ' * depth, c.co_name, hits)
    for const in c.co_consts:
        if isinstance(const, types.CodeType):
            walk(const, depth + 1)


walk(code)

out = []


def all_strs(c):
    for const in c.co_consts:
        if isinstance(const, str) and ('broadcast_playing' in const or const == 'sync'):
            out.append((c.co_name, const))
        elif isinstance(const, types.CodeType):
            all_strs(const)


all_strs(code)
print('strs', out[:40])
print('src mtime', time.ctime(os.path.getmtime(src)))
print('pyz mtime', time.ctime(os.path.getmtime(path)))
print('src count', open(src, encoding='utf-8').read().count('_last_broadcast_playing'))
