from pathlib import Path
import struct
import json

KEYS = [bytes.fromhex(x) for x in [
    '9dc6e5099bcf90125b1680ae',
    '9bf6ffd993c99951231880a8',
    '8c57ede98dcf90125b1680bf',
]]

def decompress(data, expected):
    if len(data) < 9:
        raise ValueError('Short DX compressed header')
    length, packed = struct.unpack_from('<II',data)
    if length != expected or length > 64*1024*1024 or packed > len(data) or packed < 9:
        raise ValueError('Invalid DX compressed lengths')
    marker=data[8]
    pos=9
    output=bytearray()
    def take():
        nonlocal pos
        if pos>=packed:raise ValueError('Truncated compressed command')
        v=data[pos];pos+=1;return v
    while pos<packed:
        b=take()
        if b!=marker:output.append(b)
        else:
            code=take()
            if code==marker:output.append(marker)
            else:
                code -= int(code>marker)
                count=code>>3
                if code&4:count |= take()<<5
                count+=4
                width=(code&3)+1
                if width>3:raise ValueError('Invalid DX back reference')
                distance=sum(take()<<(8*i) for i in range(width))+1
                if distance>len(output) or len(output)+count>length:
                    raise ValueError('Invalid DX back reference bounds')
                if count<=distance:
                    start=len(output)-distance
                    output.extend(output[start:start+count])
                else:
                    pattern=bytes(output[-distance:])
                    output.extend((pattern*((count+distance-1)//distance))[:count])
        if len(output)>length:raise ValueError('Decompressed data exceeds declared length')
    if len(output)!=length:raise ValueError('Wrong decompressed length')
    return bytes(output)

def read_member(path, entry):
    with Path(path).open('rb') as f:
        header=f.read(2)
        keys=[k for k in KEYS if bytes(header[i]^k[i] for i in range(2))==b'DX']
        if len(keys)!=1:raise ValueError('Unknown DX key')
        key=keys[0]
        f.seek(entry['offset'])
        raw=f.read(entry['stored_size'])
        data=bytes(b^key[(entry['offset']+i)%len(key)] for i,b in enumerate(raw))
    return decompress(data,entry['size']) if entry['compressed'] else data

def inspect(path):
    with path.open('rb') as f:
        header = f.read(28)
        keys = [k for k in KEYS if bytes(header[i]^k[i] for i in range(2)) == b'DX']
        if len(keys) != 1: raise ValueError('Unknown DX archive')
        key = keys[0]
        def read(offset, count):
            f.seek(offset)
            b = f.read(count)
            if len(b) != count: raise ValueError('Truncated archive')
            return bytes(x^key[(offset+i)%len(key)] for i,x in enumerate(b))
        magic, version, info_size, base, info, entries, folders, cp = struct.unpack('<2sH6I', read(0,28))
        if version != 4: raise ValueError('DX version '+str(version))
        entries += info
        folders += info
        def entry(offset):
            nameoff, flags, _, _, _, off, size, zsize = struct.unpack('<IIQQQIII', read(offset,44))
            pos = info + nameoff
            _, ident = struct.unpack('<HH',read(pos,4))
            name = bytearray()
            if ident:
                pos += 4
                while True:
                    b = read(pos,1)
                    pos += 1
                    if b == b'\0':break
                    name += b
                    if len(name)>4096:raise ValueError('Oversized name')
            return name.decode('cp932' if cp==932 else 'utf-8','replace'), flags, off, size, zsize
        def folderpath(offset, seen=None):
            seen = set() if seen is None else seen
            if offset in seen:raise ValueError('Folder cycle')
            seen.add(offset)
            idx, parent, count, table = struct.unpack('<4I',read(offset,16))
            name,flags,*_ = entry(entries+idx)
            path = name if flags&16 else ''
            if parent != 0xffffffff:
                prefix = folderpath(folders+parent,seen)
                path = '/'.join(x for x in [prefix,path] if x)
            return path
        results=[]
        length=path.stat().st_size
        for pos in range(folders,length,16):
            idx,parent,count,table = struct.unpack('<4I',read(pos,16))
            prefix=folderpath(pos)
            for i in range(count):
                name, flags, offset, size, zsize=entry(entries+table+44*i)
                if not flags&16:
                    results.append({'path':'/'.join(x for x in [prefix,name] if x), 'size':size, 'stored_size':size if zsize==0xffffffff else zsize, 'compressed':zsize!=0xffffffff,'offset':base+offset,'entry_offset':entries+table+44*i})
        return {'version':version,'codepage':cp,'files':results}



"""Rebuild DX4 archive members locally using the user's original archive.

Original game assets are copied on the user's machine, never included in a
distribution payload. New resources are stored uncompressed; untouched data
and directory metadata retain their original values.
"""
import hashlib
from pathlib import Path
import struct



def crypt(data,key,offset):
    return bytes(value^key[(offset+i)%len(key)] for i,value in enumerate(data))

def rebuild(source,replacements,output):
    source=Path(source)
    output=Path(output)
    if output.exists() or output.is_symlink() or source.resolve()==output.resolve():
        raise ValueError('Archive output must be a new file outside the original')
    entries=inspect(source)['files']
    by_name={e['path']:e for e in entries}
    if not replacements or set(replacements)-set(by_name):
        raise ValueError('Replacement must name existing archive members')
    with source.open('rb') as original:
        head=original.read(28)
        matching=[k for k in KEYS if crypt(head[:2],k,0)==b'DX']
        if len(matching)!=1:raise ValueError('Unknown DX archive key')
        key=matching[0]
        header=bytearray(crypt(head,key,0))
        _,version,info_size,base,info,entry_table,folder_table,codepage=struct.unpack('<2sH6I',header)
        if version!=4:raise ValueError('Only DX version 4 can be rebuilt')
        original.seek(info)
        metadata=bytearray(crypt(original.read(),key,info))
        output.parent.mkdir(parents=True,exist_ok=True)
        try:
            with output.open('xb') as target:
                original.seek(0)
                remaining=info
                while remaining:
                    block=original.read(min(1024*1024,remaining))
                    if not block:raise ValueError('Truncated original archive')
                    target.write(block)
                    remaining-=len(block)
                for name,data in sorted(replacements.items()):
                    if not isinstance(data,bytes) or len(data)>64*1024*1024:
                        raise ValueError('Invalid replacement size/type')
                    cursor=target.tell()
                    if cursor%4:
                        pad=bytes(4-cursor%4)
                        target.write(crypt(pad,key,cursor))
                        cursor=target.tell()
                    target.write(crypt(data,key,cursor))
                    entry=by_name[name]
                    at=entry['entry_offset']-info+32
                    struct.pack_into('<III',metadata,at,cursor-base,len(data),0xffffffff)
                cursor=target.tell()
                if cursor%4:
                    target.write(crypt(bytes(4-cursor%4),key,cursor))
                new_info=target.tell()
                if new_info+len(metadata)>=2**32:raise ValueError('DX4 archive exceeds address space')
                target.write(crypt(metadata,key,new_info))
                struct.pack_into('<I',header,12,new_info)
                target.seek(0)
                target.write(crypt(header,key,0))
            new=inspect(output)['files']
            if {e['path'] for e in new}!=set(by_name):raise ValueError('Archive directory changed')
            for e in new:
                if e['path'] in replacements and read_member(output,e)!=replacements[e['path']]:
                    raise ValueError('Rebuilt archive member verification failed')
        except BaseException:
            output.unlink(missing_ok=True)
            raise
    return {'members_replaced':len(replacements),'sha256':hashlib.sha256(output.read_bytes()).hexdigest()}
