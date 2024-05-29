#!/usr/bin/python3
#
# Copyright 2024 kyufie
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import re
import io
import os
import sys
import argparse
import textwrap
import shutil
from os.path import join as pjoin
import struct
import json
from PIL import Image

# TODO: Add support for JSON comments
PROG_NAME = 'hltool'
PROG_VERSION = '2.0.0'
PROG_DESC = PROG_NAME + ' extracts or creates a VFS archive file used by HL5'
PROG_HELP_EPILOG = [
    'examples:',
    ' * Extract a VFS archive data.vfs to a directory called vfs',
    '    python3 %s.py -xf data.vfs vfs' % PROG_NAME,
    ' * Create a VFS archive data.vfs from a directory called vfs',
    '    python3 %s.py -cf data.vfs vfs' % PROG_NAME
]

ENCODING = 'euc-kr'

COL_GREEN = '\033[92m'
COL_CYAN = '\033[96m'
COL_YELLOW = '\033[93m'

def cli_green(*msg):
    return COL_GREEN + ' '.join(map(str, msg)) + '\033[0m'

def cli_cyan(*msg):
    return COL_CYAN + ' '.join(map(str, msg)) + '\033[0m'

def cli_yellow(*msg):
    return COL_YELLOW + ' '.join(map(str, msg)) + '\033[0m'

def die(*msg):
    if len(msg) == 0:
        exit(1)

    print(PROG_NAME + ':', *msg, file=sys.stderr)
    exit(1)

n_warn = 0
def warn(*msg):
    global n_warn
    print(cli_yellow(*msg), file=sys.stderr)
    n_warn += 1

"""
    Creates a new directory if it doesn't exist
"""
def mkdir(path):
    if not os.path.exists(path):
        os.mkdir(path)

"""
    Read exactly size bytes from fd
    If there is not enough bytes to read, raise IOError if ignore_error == false, otherwise return None if ignore_error == True
"""
def strict_read(fd, size, ignore_error=False):
    buffer = fd.read(size)
    if len(buffer) != size:
        if not ignore_error:
            raise IOError('There is not enough bytes to read. Expecting %d bytes, got %d bytes. Probably due to malformed data at %d fd %s' % (size, len(buffer), fd.tell(), fd))
        else:
            return None

    return buffer

class Processor:
    def __init__(self, name, wdir, target_list, no_json=False, quiet=False):
        self.name = name
        self.wdir = wdir
        self.target_list = target_list
        self.no_json = no_json
        self.quiet = quiet

    def log(self, *msg):
        if not self.quiet:
            print(cli_cyan('[%s]') % self.name, *msg, flush=True)

    @staticmethod
    def convert_target_name(name):
        return os.path.basename(name) + '.json'

    def _assemble(self):
        mkdir(self.wdir)

        for target in self.target_list:
            self.log('Assemble:', target)

            in_obj = None
            out_fd = None
            if not self.no_json:
                in_fd = open(pjoin(self.wdir,
                                   self.convert_target_name(target)),
                             'r')
                in_obj = json.load(in_fd)

            out_fd = open(pjoin('.tmp', target), 'wb')

            chdir_wrap(self.wdir,
                       lambda: self.assemble(in_obj, out_fd))

            out_fd.close()
            if not self.no_json:
                in_fd.close()

    def _disassemble(self):
        mkdir(self.wdir)

        target_list = self.target_list
        for target in target_list:
            self.log('Disassemble:', target)
            in_fd = open(pjoin('raw', target), 'rb')
            if not self.no_json:
                out_fd = open(pjoin(self.wdir,
                                    self.convert_target_name(target)),
                              'w')

            out_obj = chdir_wrap(self.wdir, lambda: self.disassemble(in_fd))
            if not self.no_json:
                json.dump(out_obj, out_fd, indent=4, ensure_ascii=False)
                out_fd.close()

            in_fd.close()

    def assemble(self, in_obj, out_fd):
        raise NotImplementedError('Must implement assemble')

    def disassemble(self, in_fd):
        raise NotImplementedError('Must implement disassemble')

def read_struct(fd, format, ignore_error=False):
    read_size = struct.calcsize(format)
    buffer = strict_read(fd, read_size, ignore_error=ignore_error)
    if buffer == None:
        return None
    return struct.unpack(format, buffer)

def write_struct(fd, format, *fields):
    data = struct.pack(format, *fields)
    return fd.write(data)

"""
    Integer reading/writing utilities
"""
def read_le32(fd):
    b = read_struct(fd, '<I')
    return b[0]

def write_le32(fd, val):
    return write_struct(fd, '<I', val)

def read_le16(fd):
    b = read_struct(fd, '<H')
    return b[0]

def write_le16(fd, val):
    return write_struct(fd, '<H', val)

def read_le8(fd):
    b = read_struct(fd, '<B')
    return b[0]

def write_le8(fd, val):
    return write_struct(fd, '<B', val)

"""
    Read from fd a string table with structure as described below.

    0x00                  | str_count
    0x04                  | str_1
    0x04 + len(str_1)     | 0x00 (NULL terminator)
    0x04 + len(str_1) + 1 | str_2
    ...

    Returns a list of strings l with len(l) == str_count
"""
def read_strtab(fd):
    str_count = read_le32(fd)
    
    strings = []
    for _ in range(str_count):
        tmp = b''
        # Since we don't know the length in advance, read until we bump
        # into a NULL terminator. This is inefficient, but with the scale
        # of the things we're dealing with, this is good enough.
        while True:
            char = strict_read(fd, 1)
            if char[0] == 0:
                strings.append(tmp.decode(encoding='ascii'))
                break

            tmp += char

    return strings

def write_strtab(fd, strings, encoding='utf-8'):
    nstr = len(strings)
    write_le32(fd, nstr)

    for s in strings:
        fd.write(bytes(s, encoding) + b'\x00')

def encode_str(s):
    try:
        b = s.encode(encoding=ENCODING)
    except UnicodeDecodeError:
        warn('Unable to encode string: %s using encoding %s. The result might look slightly malformed.' % (s, ENCODING))
        b = s.encode(encoding=ENCODING, errors='ignore')

    return b

def decode_str(b):
    try:
        s = b.decode(encoding=ENCODING)
    except UnicodeDecodeError:
        warn('Unable to decode bytes: %s using encoding %s. The result might look slightly malformed.' % (b, ENCODING))
        s = b.decode(encoding=ENCODING, errors='ignore')

    return s

def read_pascal_str(fd):
    # These strings are pascal strings, which means they have leading
    # bytes which tell us the string size
    strlen = read_le8(fd)
    string_b = strict_read(fd, strlen)
    string = decode_str(string_b)
    return string

def write_pascal_str(fd, string):
    string_b = encode_str(string)
    strsize = len(string_b)
    write_le8(fd, strsize)
    return fd.write(string_b) + 1

"""
    Read a NULL-terminated string
"""
def read_str(fd):
    str_c = []

    while True:
        buf = fd.read(256)
        if len(buf) == 0:
            break

        found_null = False
        left = len(buf)
        for c in buf:
            left -= 1
            if c == 0:
                found_null = True
                break
            else:
                str_c.append(c)

        if found_null:
            # Seek fd to the first byte after the previous NULL-terminator
            fd.seek(-left, os.SEEK_CUR)
            break

    return decode_str(bytes(str_c))

"""
    Write a NULL-terminated string
"""
def write_str(fd, string):
    str_b = encode_str(string) + b'\x00'
    return fd.write(str_b)

"""
    Read array from fd with structure as described below:
    |--------------------------
    | OFFSET
    |--------------------------
    | 0x0000 | n_elements
    | 0x0002 | elem_1_length
    | 0x0004 | elem_1_data
    | 0x.... | elem_2_length
    | 0x.... | elem_2_data
    | 0x.... | ....
    |--------------------------

    This function calls read_cb for every element of the array with a
    temporary file descriptor allocated containing the element's data
    as its first argument.
    After read_cb returns, its return value will be collected and
    eventually returned.

    Use wide_spec=True if the array uses 32-bit length specifier.
    Use pass_idx=True to pass the current index to the callback
    as a second argument.

    On success, it returns a list of the return values of each call to
    read_cb.
"""
def read_pascal_array(fd, read_cb, wide_spec=False, pass_idx=False):
    ret_vals = []
    n_elem = read_le32(fd) if wide_spec else read_le16(fd)

    for i in range(n_elem):
        datalen = read_le32(fd) if wide_spec else read_le16(fd)

        tmp_fd = io.BytesIO(strict_read(fd, datalen))
        if pass_idx:
            ret_vals.append(read_cb(tmp_fd, i))
        else:
            ret_vals.append(read_cb(tmp_fd))
        tmp_fd.close()

    return ret_vals

def write_pascal_array(fd, write_cb, elements, wide_spec=False, pass_idx=False):
    total_bytes_written = 0

    n_elem = len(elements)
    if wide_spec:
        write_le32(fd, n_elem)
        total_bytes_written += 4
    else:
        write_le16(fd, n_elem)
        total_bytes_written += 2

    for e in elements:
        start_pos = fd.tell()
        fd.seek(4 if wide_spec else 2, os.SEEK_CUR)
        total_bytes_written += 4 if wide_spec else 2

        if pass_idx:
            bytes_written = write_cb(fd, e, i)
        else:
            bytes_written = write_cb(fd, e)

        fd.seek(start_pos, os.SEEK_SET)
        if wide_spec:
            write_le32(fd, bytes_written)
        else:
            write_le16(fd, bytes_written)
        fd.seek(bytes_written, os.SEEK_CUR)

        total_bytes_written += bytes_written

    return total_bytes_written

def read_ext_array(fd, read_cb, lspec_size):
    lspec_fmt = '<%dH' if lspec_size == 2 else '<%dB'

    ret_vals = []
    n_elem = read_le8(fd)

    len_list = read_struct(fd, lspec_fmt % n_elem)
    for l in len_list:
        tmp_fd = io.BytesIO(strict_read(fd, l))
        ret_vals.append(read_cb(tmp_fd))
        tmp_fd.close()

    return ret_vals

def write_ext_array(fd, write_cb, lspec_size, elements):
    n_elem = len(elements)
    lspec_fmt = '<%dH' % n_elem if lspec_size == 2 else '<%dB' % n_elem

    write_le8(fd, n_elem)
    lspec_pos = fd.tell()
    fd.seek(struct.calcsize(lspec_fmt), io.SEEK_CUR)
    
    len_list = []
    for e in elements:
        bytes_written = write_cb(fd, e)
        len_list.append(bytes_written)
    
    total_bytes_written = sum(len_list)

    fd.seek(lspec_pos, os.SEEK_SET)
    write_struct(fd, lspec_fmt, *len_list)
    fd.seek(total_bytes_written, os.SEEK_CUR)

def reorder_dict(unordered_dict, key_order):
    # Make sure that the key in key_order is exactly the same as in dict
    assert(set(unordered_dict) == set(key_order))
    return {k: unordered_dict[k] for k in key_order}

class SimpleStruct:
    def __init__(self, struct_dict, key_order=None):
        assert(isinstance(struct_dict, dict))
        self.struct_dict = struct_dict
        self.key_order = key_order

    def read(self, fd):
        ret = {}
        for k, v in self.struct_dict.items():
            ret[k] = v.read(fd)

        if self.key_order:
            ret = reorder_dict(ret, self.key_order)

        return ret

    def write(self, fd, in_obj):
        bytes_written = 0
        for k, v in self.struct_dict.items():
            bytes_written += v.write(fd, in_obj[k])

        return bytes_written

"""
    Pascal Array (not an actual term, adapted from pascal string)
    For more information, please look into read_pascal_array
"""
class PascalArray:
    def __init__(self, struct_dict, key_order=None):
        assert(isinstance(struct_dict, dict))
        self.struct_dict = struct_dict
        self.key_order = key_order

    def read(self, fd):
        def read_func(in_fd):
            item = {}
            for k, v in self.struct_dict.items():
                item[k] = v.read(in_fd)
            
            if self.key_order:
                item = reorder_dict(item, self.key_order)

            return item

        return read_pascal_array(fd, read_func)

    def write(self, fd, in_obj):
        def write_func(in_fd, item):
            bytes_written = 0
            for k, v in self.struct_dict.items():
                bytes_written += v.write(fd, item[k])
            return bytes_written

        return write_pascal_array(fd, write_func, in_obj)

class PascalStr:
    def read(self, fd):
        # These strings are pascal strings, which means they have leading
        # byte which tells us the string size
        strlen = read_le8(fd)
        string_b = strict_read(fd, strlen)
        string = decode_str(string_b)

        return string

    def write(self, fd, string):
        string_b = encode_str(string)
        strsize = len(string_b)
        write_le8(fd, strsize)
        return fd.write(string_b) + 1

class Data:
    def __init__(self, size=None):
        self.size = size

    def read(self, fd):
        if self.size == None:
            return list(fd.read())
        else:
            return list(strict_read(fd, self.size))

    def write(self, fd, in_obj):
        return fd.write(bytes(in_obj))

class Int:
    def __init__(self, bits, byteorder='little', signed=False):
        if byteorder != 'little' and byteorder != 'big':
            raise ValueError('Byteorder must be either "little" or "big"')
        if bits % 8 or bits == 0:
            raise ValueError('bits must be a multiple of 8 and != 0')

        self.byteorder = byteorder
        self.bits = bits
        self.signed = signed

    def read(self, fd):
        struct_fmt = '%dB' % (self.bits // 8)
        ints = read_struct(fd, struct_fmt)
        return int.from_bytes(bytes(ints), byteorder=self.byteorder,
                              signed=self.signed)

    def write(self, fd, in_obj):
        ints = int.to_bytes(in_obj, length=self.bits // 8,
                            byteorder=self.byteorder,
                            signed=self.signed)
        return fd.write(ints)

"""
    Recursively lists all files under @path
"""
def list_files_recursive(path='.'):
    files = []
    entries = os.listdir(path)
    for entry in entries:
        if os.path.isdir(pjoin(path, entry)):
            subdir_files = list_files_recursive(pjoin(path, entry))
            files.extend([pjoin(entry, f) for f in subdir_files])
        else:
            files.append(entry)
    return files

"""
    Calculates file descriptor size relative to seek position 0
"""
def get_file_size(fd):
    prev_pos = fd.tell()
    fd.seek(0, os.SEEK_END)
    size = fd.tell()
    fd.seek(prev_pos, os.SEEK_SET)
    return size

class QuestProcessor(Processor):
    def __init__(self, **kwargs):
        self.struct = PascalArray({
            'data1': Data(3),
            'name': PascalStr(),
            'desc': PascalStr(),
            'type': PascalStr(),
            'data2': Data(38)
        }, ['name', 'desc', 'type', 'data1', 'data2'])

        target_list = ['c/csv/quest_%d.dat' % i for i in range(3)]
        super().__init__('questproc', 'quest', target_list, **kwargs)

    def disassemble(self, in_fd):
        return self.struct.read(in_fd)

    def assemble(self, in_obj, out_fd):
        self.struct.write(out_fd, in_obj)

class EnemyProcessor(Processor):
    def __init__(self, **kwargs):
        self.struct = PascalArray({
            'name': PascalStr(),
            'param_0h': Int(8),
            'level': Int(8),
            'param_2h': Int(8),
            'param_3h': Int(8),
            'atk': Int(16),
            'param_6h': Int(8),
            'param_7h': Int(8),
            'param_8h': Int(16),
            'param_ah': Int(16),
            'param_ch': Int(8),
            'param_dh': Int(16),
            'param_fh': Int(8),
            'param_10h': Int(8),
            'param_11h': Int(16),
            'param_13h': Int(16),
            'param_15h': Int(8),
            'param_16h': Int(16),
            'param_18h': Int(8),
            'param_19h': Int(8),
            'param_1ah': Int(16),
            'param_1ch': Int(16),
            'param_1eh': Int(8),
            'param_1fh': Int(16),
            'param_21h': Int(8),
            'param_22h': Int(8),
            'param_23h': Int(16),
            'param_25h': Int(16),
            'param_27h': Int(32),
            'param_2bh': Int(32),
            'param_2fh': Int(32),
            'param_33h': Int(32),
            'param_37h': Int(32),
            'param_3bh': Int(32),
            'param_3fh': Int(32),
            'param_43h': Int(8),
            'param_44h': Int(8),
            'param_45h': Int(8),
            'param_46h': Int(8),
            'param_47h': Int(8),
            'param_48h': Int(8),
            'param_49h': Int(8),
            'param_4ah': Int(8),
            'param_4bh': Int(16),
            'param_4dh': Int(8),
            'param_4eh': Int(8),
            'param_4fh': Int(8),
            'hp': Int(32),
            'param_54h': Int(16),
            'param_56h': Int(16),
            'param_58h': Int(16),
            'param_5ah': Int(16),
            'param_5ch': Int(16),
            'param_5eh': Int(16),
            'param_60h': Int(16),
            'param_62h': Int(16),
            'param_64h': Int(16),
            'param_66h': Int(16),
            'param_68h': Int(16),
            'param_6ah': Int(16),
            'param_6ch': Int(16),
            'param_6eh': Int(16),
            'param_70h': Int(16),
            'param_72h': Int(16),
            'param_74h': Int(16),
            'param_76h': Int(8),
            'param_77h': Int(8),
            'param_78h': Int(8),
            'param_79h': Int(8),
            'param_7ah': Int(8),
            'param_7bh': Int(8),
            'param_7ch': Int(8),
            'param_7dh': Int(16),
            'param_7fh': Int(8),
        })

        target_list = ['c/csv/enemy_0.dat',
                       'c/csv/enemy_1.dat',
                       'c/csv/enemy_2.dat',
                       'c/csv/enemy_expert_0.dat',
                       'c/csv/enemy_expert_1.dat',
                       'c/csv/enemy_expert_2.dat']
        super().__init__('enemyproc', 'enemy', target_list, **kwargs)

    def disassemble(self, in_fd):
        return self.struct.read(in_fd)

    def assemble(self, in_obj, out_fd):
        self.struct.write(out_fd, in_obj)

class ClassProcessor(Processor):
    def __init__(self, **kwargs):
        self.struct = PascalArray({
            'name': PascalStr(),
            'data': Data(59)
        })

        target_list = ['c/csv/class.dat']
        super().__init__('classproc', 'misc', target_list, **kwargs)

    def disassemble(self, in_fd):
        return self.struct.read(in_fd)

    def assemble(self, in_obj, out_fd):
        self.struct.write(out_fd, in_obj)

class SkillProcessor(Processor):
    def __init__(self, **kwargs):
        self.struct = PascalArray({
            'name': PascalStr(),
            'data': Data(47),
            'desc': PascalStr()
        }, ['name', 'desc', 'data'])

        target_list = ['c/csv/skill_00.dat',
                       'c/csv/skill_01.dat',
                       'c/csv/skill_02.dat',
                       'c/csv/skill_03.dat',
                       'c/csv/skill_05.dat']
        super().__init__('skillproc', 'skill', target_list, **kwargs)

    def disassemble(self, in_fd):
        return self.struct.read(in_fd)

    def assemble(self, in_obj, out_fd):
        self.struct.write(out_fd, in_obj)

class CommonTextProcessor(Processor):
    def __init__(self, **kwargs):
        target_list = ['c/csv/common_text.dat',
                       'c/csv/name.dat',
                       'c/csv/mission_text.dat',
                       'c/csv/menu_text.dat',
                       'c/csv/ingame_text.dat',
                       'c/csv/tips.dat']
        super().__init__('commontextproc', 'common_text', target_list, **kwargs)

    def assemble(self, in_obj, out_fd):
        write_pascal_array(out_fd, write_pascal_str, in_obj)

    def disassemble(self, in_fd):
        return read_pascal_array(in_fd, read_pascal_str)

"""
    Every item in the game belongs to a certain group with ids ranging
    from 0-18.
    Each groups are separated by file with different structures.
    
    List of group ids and their description:
    * 00-10: Equipment
    *    11: Battle use item (consumables)
    *    12: Orb
    *    13: Called "Mix Item" in the game code (actually "materials")
    *    14: Slightly different mix item?
    *    15: Mix book item? (I think it's recipe)
    * 16-17: Skill book
    *    18: Cash? (Items obtained from the shop)

    The file contains an array with each elements correspond to an
    item present in the game.
    These items are identified internally by the order in which they
    appear in the file.

    The element's structure can be summarized into two parts:
    * General information
      Contains general information about an item such as name,
      and description. It applies to every item groups and has a fixed
      structure.
    * Group specific information
      Contains specific information about an item with different structure
      for each groups. It defines the item's attributes relevant to the
      group it belongs.

    TODO: It seems that the game uses a static array to contain the
    item's name and description, which means there should be a limit
    on how long those strings can be without crashing the game.
"""
class ItemProcessor(Processor):
    def __init__(self, **kwargs):
        self.init_structs()

        target_list = ['c/csv/item_%02d.dat' % i for i in range(19)]
        super().__init__('itemproc', 'item', target_list, **kwargs)

    def init_structs(self):
        self.struct_general = PascalArray({
            'type_id': Int(16),
            'name': PascalStr(),
            'price': Int(32),
            'desc': PascalStr(),
        }, ['name', 'desc', 'price', 'type_id', 'extras'])

        struct_equipment = SimpleStruct({
            'sprite_id': Int(16),
            'sprite_color_effect': Int(16),
            'atk_speed': Int(8), # 1: Fast, 0: Slow
            'class': Int(8),
            'min_atk/phys_def': Int(16),
            'max_atk/magic_def': Int(16),
            'param_ah': Int(16),
            'param_ch': Int(8),
            'param_dh': Int(8),
            'param_eh': Int(8),
            'param_fh': Int(8),
            'param_10h': Int(8),
            'param_11h': Int(8),
            'param_12h': Int(8),
            'param_13h': Int(8),
            'param_14h': Int(8)
        })

        self.group_structs = dict.fromkeys(range(11),
                                           struct_equipment)

    @staticmethod
    def get_item_gid(fd):
        id_match = re.match('item_([0-9]*)\\.dat',
                            os.path.basename(fd.name))
        assert(id_match)
        return int(id_match.groups()[0])

    def disassemble(self, in_fd):
        item_gid = self.get_item_gid(in_fd)

        # XXX: This looks like a hack to me, although subtle
        if item_gid in self.group_structs:
            self.struct_general.struct_dict['extras'] = self.group_structs[item_gid]
        else:
            self.struct_general.struct_dict['extras'] = Data()

        item_obj = self.struct_general.read(in_fd)

        return item_obj

    def assemble(self, in_obj, out_fd):
        item_gid = self.get_item_gid(out_fd)
        
        # XXX: This looks like a hack to me
        if item_gid in self.group_structs:
            self.struct_general.struct_dict['extras'] = self.group_structs[item_gid]
        else:
            self.struct_general.struct_dict['extras'] = Data()

        self.struct_general.write(out_fd, in_obj)

"""
    This class provides base functionality to work with gbm image
"""
class GbmImg:
    GBM_HEADER = SimpleStruct({
        # I don't know if 'color' is accurate, as there is actually another data
        # embedded in it with purpose that's currently unbeknownst to me
        'color': Int(8),
        'palette_size': Int(8),
        'width': Int(16),
        'height': Int(16)
    })

    def __init__(self, gbm_fd=None):
        if gbm_fd:
            self.open(gbm_fd)

    # TODO: color_bit and unk0 args should be in save
    def from_png(self, png_fd, color_bit, unk0):
        # Converts pillow RGBA Image data into a list of pixel values suitable
        # for GBM encoded as 565 RGB color.
        def to_gbm_color(pil_imgdata):
            def pix_to_gbm(rgba):
                r, g, b, a = rgba

                if a == 0:
                    return 0xf81f

                r = round(r / (0xff/0x1f))
                g = round(g / (0xff/0x3f))
                b = round(b / (0xff/0x1f))

                return b & 0x1f | (g & 0x3f) << 5  | (r & 0x1f) << 11

            return [pix_to_gbm(p) for p in pil_imgdata]

        img = Image.open(png_fd, formats=['png'])
        imgdata = list(img.getdata())

        if color_bit != 8 and color_bit != 4:
            die('color_bit must be either 4 or 8')

        self.unk0 = unk0
        self.color_bit = color_bit
        self.width = img.width
        self.height = img.height

        # List of pixel values. We will convert it to a list of indices each pointing
        # to an entry in the pallete data
        gbm_imgdata = to_gbm_color(imgdata)
        gbm_palette_data = list(set(gbm_imgdata))

        if len(gbm_palette_data) > 256:
            die('PNG image %s has more than 256 colors, please use image that uses 256 colors or less' % png_fd)

        # Palette to index conversion table
        p_i = dict((p, i) for i, p in enumerate(gbm_palette_data))
        self.pixel_data = [p_i[p] for p in gbm_imgdata]
        self.palette_data = gbm_palette_data

    def open(self, gbm_fd):
        def read_4bit_pixdata(fd, width, height):
            # These pixels are encoded in the form of a matrix of color indices.
            # Each byte represents two pixels.
            # If there are odd number of pixels in a row, the last
            # byte of the row will only have a half of it occupied by a pixel.
            # In that case, the remaining half will be zeroed out and unused
            # during rendering.
            #
            # Example 5x2 image:
            # ff ff f0
            # ff ff f0
            # Example 6x2 image:
            # ff ff ff
            # ff ff ff
            #
            # Which means for an image with an odd number of width, we will
            # have exactly "height" number of unused pixels.
            odd_row = width % 2 != 0
            # Odd npixel can only be produced if both width and height are odd numbers.
            # In the case of odd npixel, adding already odd height will make it even.
            # Which means npixel will always be even no matter what.
            npixel = width * height
            if odd_row:
                npixel += height

            pixel_data = []
            # Loop for each row
            for _ in range(height):
                row_data = list(read_struct(fd, '<%dB' % (npixel // height // 2)))

                for b in row_data:
                    high = b >> 4
                    low = b & 0xf
                    pixel_data.append(high)
                    pixel_data.append(low)

                # Remove the unused pixel
                if odd_row:
                    pixel_data.pop()

            return pixel_data

        header = self.GBM_HEADER.read(gbm_fd)
        # The game uses the first 4 bits to specify color resolution
        self.color_bit = header['color'] & 0xf
        # Unknown variable, but we'll try to preserve its value just in case
        self.unk0 = header['color'] >> 4

        if self.color_bit != 8 and self.color_bit != 4:
            die('Unsupported color resolution', self.color_bit)

        # Read color palette
        palette_size = header['palette_size']
        self.palette_data = read_struct(gbm_fd, '<%dH' % palette_size)

        # Read pixel data
        self.width = header['width']
        self.height = header['height']
        if self.color_bit == 8:
            self.pixel_data = read_struct(gbm_fd, '<%dB' % (self.width * self.height))
        elif self.color_bit == 4:
            self.pixel_data = read_4bit_pixdata(gbm_fd, self.width, self.height)

    def save(self, gbm_fd):
        bytes_written = 0

        header = {
            'color': self.unk0 << 4 | self.color_bit & 0xf,
            'width': self.width,
            'height': self.height,
            'palette_size': len(self.palette_data)
        }
        bytes_written += self.GBM_HEADER.write(gbm_fd, header)
        bytes_written += write_struct(gbm_fd, '<%dH' % len(self.palette_data), *self.palette_data)

        if self.color_bit == 8:
            bytes_written += write_struct(gbm_fd, '<%dB' % len(self.pixel_data), *self.pixel_data)
        elif self.color_bit == 4:
            odd_row = self.width % 2 != 0
            npixel = len(self.pixel_data)

            if not odd_row:
                pixel_data = []
                for i in range(npixel // 2):
                    high, low = self.pixel_data[i*2 : (i+1)*2]
                    pixel_data.append((high & 0xf) << 4 | (low & 0xf))

                bytes_written += write_struct(gbm_fd, '<%dB' % (npixel // 2), *pixel_data)
                return bytes_written

            # Odd row
            for i in range(self.height):
                row_data = self.pixel_data[i*self.width : (i+1)*self.width]
                # Add a dummy pixel to make it even
                row_data.append(0)

                pixel_data = []
                for j in range(len(row_data) // 2):
                    high, low = row_data[j*2 : (j+1)*2]
                    pixel_data.append((high & 0xf) << 4 | (low & 0xf))

                bytes_written += write_struct(gbm_fd, '<%dB' % (len(row_data) // 2), *pixel_data)
        else:
            assert(False)
        
        return bytes_written

    def to_png(self, out_fd):
        # Given palette data and pixel data, return a pillow-compatible
        # RGBA Image data.
        def pix_to_rgba(palette_data, pixel_data):
            # This function converts 16-bit GBM color into RGBA
            def to_rgba(c):
                b = c & 0x1f
                g = c >> 5 & 0x3f
                r = c >> 11 & 0x1f
                a = 0 if c == 0xf81f else 255

                # Since the encoded color uses less than 8-bit color space, the color will appear
                # dark in 8-bit color space. We need to maintain their proportion over
                # their original color space somehow.
                r = round(r * (0xff/0x1f))
                g = round(g * (0xff/0x3f))
                b = round(b * (0xff/0x1f))
                return (r, g, b, a)

            rgba_palette = [to_rgba(color) for color in palette_data]
            rgba_pixel = [rgba_palette[idx] for idx in pixel_data]
            return rgba_pixel

        def rgba_to_bytes(rgba_data):
            flat_list = []
            for data in rgba_data:
                flat_list.extend(data)
            return bytes(flat_list)

        # Convert pixel data
        imgdata = pix_to_rgba(self.palette_data, self.pixel_data)
        img_data = rgba_to_bytes(imgdata)

        # Construct new image
        img = Image.new('RGBA', (self.width, self.height))
        img.frombytes(img_data)
        img.save(out_fd, format='png')

class GbmProcessor(Processor):
    def __init__(self, **kwargs):
        target_list = ['c/map/face_%02d.gbm' % i for i in range(22)]
        target_list += ['c/map/fgi_%03d.gbm' % i for i in range(3)]
        target_list += ['c/map/obj_%03d.gbm' % i for i in range(255)]
        target_list += ['c/map/tile_%03d.gbm' % i for i in range(62)]
        super().__init__('gbmproc', 'gbm_sprites', target_list, **kwargs)

    def disassemble(self, in_fd):
        png_fd = open(os.path.basename(in_fd.name) + '.png', 'wb')

        gbm_img = GbmImg(in_fd)
        gbm_img.to_png(png_fd)

        return {
            'color_bit': gbm_img.color_bit,
            'unk0': gbm_img.unk0
        }

    def assemble(self, in_obj, out_fd):
        color_bit = in_obj['color_bit']
        unk0 = in_obj['unk0']

        png_fd = open(os.path.basename(out_fd.name) + '.png', 'rb')

        gbm_img = GbmImg()
        gbm_img.from_png(png_fd, color_bit, unk0)
        gbm_img.save(out_fd)

class MgrProcessor(Processor):
    def __init__(self, **kwargs):
        target_list = ['c/sp/img0/%03d.mgr' % i for i in range(128)]
        target_list.pop(3)
        target_list += ['c/sp/img1/%03d.mgr' % i for i in range(57)]
        target_list.pop(140)
        target_list += ['c/sp/img2/%03d.mgr' % i for i in range(49)]
        target_list += ['c/sp/img3/%03d.mgr' % i for i in range(49)]
        target_list += ['c/sp/img4/%03d.mgr' % i for i in range(68)]
        target_list += ['c/sp/img5/%03d.mgr' % i for i in range(26)]
        target_list += ['c/sp/img6/%03d.mgr' % i for i in range(17)]
        target_list += ['c/par/pimg%02d.mgr' % i for i in range(9)]
        target_list += ['c/img/gmenu.mgr',
                        'c/img/icon.mgr',
                        'c/img/menu.mgr',
                        'c/img/shadow.mgr',
                        'c/img/touch.mgr',
                        'c/img/ui.mgr',
                        'c/img/worldmap.mgr']
        target_list += ['c/map_sp/fgi_img00.mgr',
                        'c/map_sp/ms_img00.mgr',
                        'c/map_sp/ms_img01.mgr',
                        'c/map_sp/ms_img02.mgr',
                        'c/map_sp/ms_img03.mgr',
                        'c/map_sp/ms_img09.mgr']

        # We'll handle json processing ourselves
        super().__init__('mgrproc', 'mgr_sprites', target_list, no_json=True, **kwargs)

    def disassemble(self, in_fd):
        # We should be inside self.wdir right now
        mgr_dirname = os.path.basename(os.path.dirname(in_fd.name))
        mkdir(mgr_dirname)
        mgr_basename = os.path.basename(in_fd.name)
        mkdir(pjoin(mgr_dirname, mgr_basename))

        png_list = []

        def to_png(gbm_fd, idx):
            png_path = pjoin(mgr_dirname, mgr_basename, '%d.png' % idx)
            png_fd = open(png_path, 'wb')

            gbm_img = GbmImg(gbm_fd)
            gbm_img.to_png(png_fd)
            
            png_list.append({
                'path': '%d.png' % idx,
                'unk0': gbm_img.unk0,
                'color_bit': gbm_img.color_bit
            })

            png_fd.close()

        read_pascal_array(in_fd, to_png, wide_spec=True, pass_idx=True)

        with open(pjoin(mgr_dirname, mgr_basename, 'mgr.json'), 'w') as f:
            json.dump(png_list, f)

    def assemble(self, in_obj, out_fd):
        # We should be inside self.wdir right now
        mgr_dirname = os.path.basename(os.path.dirname(out_fd.name))
        mgr_basename = os.path.basename(out_fd.name)

        with open(pjoin(mgr_dirname, mgr_basename, 'mgr.json'), 'r') as f:
            png_list = json.load(f)

        def from_png(fd, png_obj):
            png_path = pjoin(mgr_dirname, mgr_basename, png_obj['path'])
            color_bit = png_obj['color_bit']
            unk0 = png_obj['unk0']

            png_fd = open(png_path, 'rb')

            gbm_img = GbmImg()
            gbm_img.from_png(png_fd, color_bit, unk0)
            bytes_written = gbm_img.save(fd)

            png_fd.close()
            return bytes_written

        write_pascal_array(out_fd, from_png, png_list, wide_spec=True)

"""
    A scene file defines various parameters regarding a scene/set.

    The file consists of a header, and three extended arrays.
    The first two arrays are still unknown, whereas the last
    array contains a collection of strings such as the set's name and dialogues.
    The first string is used to store the set's name, if it happens to be an unnamed
    set, then the name would be "0".
"""
class SceneProcessor(Processor):
    HEADER_STRUCT_FMT = '<15B'

    def __init__(self, **kwargs):
        target_list = ['c/map/%05d.scn' % i for i in range(218)]
        super().__init__('sceneproc',
                         'scene',
                         target_list, **kwargs)

    def disassemble(self, in_fd):
        header = read_struct(in_fd, self.HEADER_STRUCT_FMT)
        # The first three bytes of the header are associated with each array and 
        # they determine the number of bits the array is using for its length
        # specifier. 1 for 8-bit, 2 for 16-bit.
        lspec_size = header[:3]
        arr1 = read_ext_array(in_fd, lambda fd: fd.read(), lspec_size[0])
        arr2 = read_ext_array(in_fd, lambda fd: fd.read(), lspec_size[1])
        strings = read_ext_array(in_fd, read_str, lspec_size[2])

        return {
            'strings': strings,
            'header': list(header),
            'arr1': [list(d) for d in arr1],
            'arr2': [list(d) for d in arr2]
        }

    def assemble(self, in_obj, out_fd):
        header = in_obj['header']
        arr1 = [bytes(d) for d in in_obj['arr1']]
        arr2 = [bytes(d) for d in in_obj['arr2']]
        strings = in_obj['strings']
        lspec_size = header[:3]

        out_fd.write(bytes(header))
        write_ext_array(out_fd, lambda fd, data: fd.write(data),
                        lspec_size[0], arr1)
        write_ext_array(out_fd, lambda fd, data: fd.write(data),
                        lspec_size[1], arr2)
        write_ext_array(out_fd, write_str, lspec_size[2], strings)

"""
    VFS

    VFS is similar to that of tarball, in which it creates a
    collection of files packed into a single file.

    Internally, VFS stores its data in the form of an array where
    each element is a structure made for each file with a header
    that stores information about the file and the file content
    itself.

    Each file is tagged with a number to address them.
    This number is obtained by invoking a hash algorithm on the full
    path of the file inside the archive.

    Imagine a VFS with two files.
    | Offset | 
    | 0x0000 | file_1_path_hash
    | 0x0004 | file_1_file_size
    | 0x0008 | file_1_content
    | ...... | file_2_path_hash
    | ...... | .....
"""
class VFSProcessor:
    MANIFEST_PATH_HASH = 0xbc909d54
    HEADER_STRUCT_FMT = '<II'

    def __init__(self, quiet=False):
        self.name = 'vfsproc'
        self.quiet = quiet
    
    def log(self, *msg):
        if not self.quiet:
            print(cli_cyan('[%s]') % self.name, *msg, flush=True)

    def get_vfs_data(self, vfs_fd):
        data = {}
        while True:
            header = read_struct(vfs_fd, self.HEADER_STRUCT_FMT,
                                 ignore_error=True)
            if header == None:
                break
            path_hash, file_size = header

            data[path_hash] = {
                'file_size': file_size,
                'offset': vfs_fd.tell()
            }

            # Jump to the next file header
            vfs_fd.seek(file_size, os.SEEK_CUR)

        return data

    def assemble(self, vfs_fd):
        def append_to_vfs(_vfs_fd, _fd, fhash):
            fsize = get_file_size(_fd)
            write_struct(_vfs_fd, self.HEADER_STRUCT_FMT,
                         fhash, fsize)
            _vfs_fd.write(_fd.read())

        path_list = list_files_recursive()
        for path in path_list:
            self.log('Packing:', path)

            fd = open(path, 'rb')
            append_to_vfs(vfs_fd, fd, self.hash(path))
            fd.close()

        strtab_fd = io.BytesIO()
        write_strtab(strtab_fd, path_list)
        strtab_fd.seek(0, os.SEEK_SET)

        append_to_vfs(vfs_fd, strtab_fd, self.MANIFEST_PATH_HASH)
        strtab_fd.close()

    def disassemble(self, vfs_fd):
        vfs_data = self.get_vfs_data(vfs_fd)

        manifest = vfs_data[self.MANIFEST_PATH_HASH]
        vfs_fd.seek(manifest['offset'], os.SEEK_SET)
        filenames = read_strtab(vfs_fd)

        for fname in filenames:
            self.log('Extracting:', fname)

            path_hash = self.hash(fname)
            file = vfs_data[path_hash]

            if os.path.dirname(fname) != '':
                os.makedirs(os.path.dirname(fname), exist_ok=True)
            
            vfs_fd.seek(file['offset'], os.SEEK_SET)
            file_data = vfs_fd.read(file['file_size'])

            file_fd = open(os.path.relpath(fname), 'wb')
            file_fd.write(file_data)

        return filenames

    # A simple hashing algorithm
    @staticmethod
    def hash(string):
        acc = 0x1505
        for c in bytes(string, 'ascii'):
            acc += c + (acc << 5)
            # Keep it 32-bit
            acc &= 0xffffffff
        return acc

# Chdir to dir, execute func, then go back
def chdir_wrap(dir, func):
    mkdir(dir)
    prev_dir = os.getcwd()
    os.chdir(dir)
    ret = func()
    os.chdir(prev_dir)
    return ret

class HL5Tool:
    processors = [
        CommonTextProcessor,
        SceneProcessor,
        QuestProcessor,
        EnemyProcessor,
        ClassProcessor,
        SkillProcessor,
        ItemProcessor,
        MgrProcessor,
        GbmProcessor
    ]

    def __init__(self, vfs_fd, base_dir, quiet=False):
        self.vfs_fd = vfs_fd
        self.base_dir = base_dir
        self.quiet = quiet

        mkdir(base_dir)

    def get_dir(self, dir_name='.'):
        path = pjoin(self.base_dir, dir_name)
        mkdir(path)
        return path

    def open_meta(self, mode):
        return open(pjoin(self.get_dir(), 'vfs.json'), mode)

    def extract(self, raw_only=False):
        # Prepare meta information
        meta = {
            'version': PROG_VERSION,
            'raw_only': raw_only
        }
        with self.open_meta('w') as fd:
            json.dump(meta, fd)

        # Extract VFS
        vfs_proc = VFSProcessor(quiet=self.quiet)
        chdir_wrap(self.get_dir('raw'),
                   lambda: vfs_proc.disassemble(self.vfs_fd))

        # Don't decompile anything if not requested
        if raw_only:
            return

        for proc in self.processors:
            proc = proc(quiet=self.quiet)
            chdir_wrap(self.base_dir, proc._disassemble)

    def create(self):
        with self.open_meta('r') as fd:
            meta = json.load(fd)
            if PROG_VERSION != meta['version']:
                die('Incompatible version number in the extracted archive, expected %s but got %s'
                    % (PROG_VERSION, meta['version']))
            raw_only = meta['raw_only']

        if raw_only:
            vfs_proc = VFSProcessor(quiet=self.quiet)
            chdir_wrap(self.get_dir('raw'),
                       lambda: vfs_proc.assemble(self.vfs_fd))
        else:
            # Make a copy of the raw directory.
            # Later, we will replace some of the files with our newly assembled files.
            #
            # TODO: This removes everything under .tmp and it's dangerous.
            #       Fuck user's files if they happens to be there.
            if os.path.exists(self.get_dir('.tmp')):
                shutil.rmtree(self.get_dir('.tmp'))
            shutil.copytree(self.get_dir('raw'), self.get_dir('.tmp'),
                            dirs_exist_ok=True)

            for proc in self.processors:
                proc = proc(quiet=self.quiet)
                chdir_wrap(self.base_dir, proc._assemble)

            vfs_proc = VFSProcessor(quiet=self.quiet)
            chdir_wrap(self.get_dir('.tmp'),
                       lambda: vfs_proc.assemble(self.vfs_fd))

def print_version():
    # Version information
    print(PROG_NAME, PROG_VERSION)
    # Copyright information
    print('Copyright (C) 2024 kyufie')
    print('License GPLv3+: GNU GPL version 3 or later <https://gnu.org/licenses/gpl.html>.')
    print('This is free software: you are free to change and redistribute it.')
    print('There is NO WARRANTY, to the extent permitted by law.')

    exit(0)


def main():
    parser = argparse.ArgumentParser(
        description=PROG_DESC,
        formatter_class=argparse.RawTextHelpFormatter,
        epilog='\n'.join(PROG_HELP_EPILOG))

    parser.add_argument('-x', '--extract', action='store_true',
                        help='extract a VFS archive')
    parser.add_argument('-c', '--create', action='store_true',
                        help='create a VFS archive')
    parser.add_argument('-f', '--file', metavar='ARCHIVE',
                        help='use archive file ARCHIVE')
    parser.add_argument('-r', '--raw', action='store_true',
                        help='only extract raw files')
    parser.add_argument('-q', '--quiet', action='store_true',
                        help='do not log anything except warnings and errors')
    parser.add_argument('-v', '--version', action='store_true',
                        help='output version information and exit')
    parser.add_argument('dir', nargs='?',
                        help='path to directory where files are read from or written to (if not specified, defaults to the current working directory)')

    args = parser.parse_args()

    if args.version:
        print_version()

    if args.extract and args.create:
        die('You may not specify more than one actions (-xc)')

    if args.extract:
        if args.file == None:
            vfs_fd = sys.stdin
        else:
            vfs_fd = open(args.file, 'rb')
    elif args.create:
        if args.file == None:
            vfs_fd = sys.stdout
        else:
            vfs_fd = open(args.file, 'wb')
    else:
        parser.print_help()
        die()

    base_dir = os.getcwd() if args.dir == None else args.dir

    tool = HL5Tool(vfs_fd, base_dir, quiet=args.quiet)
    if args.create:
        tool.create()
    elif args.extract:
        tool.extract(raw_only=args.raw)

    if n_warn:
        print('Program finished with %d warning(s)' % n_warn)

if __name__ == "__main__":
    main()
