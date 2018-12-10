# -*- coding: utf-8 -*-
"""
A tool that merge multiple COMPRESSED .KTX files into a single .KTX file, using either array or cubemaps.

This tool was created to work around Compressonator ( https://github.com/GPUOpen-Tools/Compressonator ) missing features. It do not currently
supports the generation of texture array or cubemap textures from its command tool utility (although I raised an issue and that might change in the future).

Usage:

Output types:

* array: Outputs a texture file representing a 2D texture array. Index of inputs file are used for array indices
* cube: Output a cubemap file. The order of inputs must be "+X,-X,+Y,-Y,+Z,-Z" aka "Right, Left, Top, Bottom, Front, Back"


Auto Mode:

Auto mode lets you pass a wildcard pattern instead of a list of input files. 
Ktxmerge use the files name in order to sort the inputs.  See the "Wildcard patterns" section for more info


Wildcard patterns:

example with pattern "item_*"

* array: item_1.ktx, item_2.ktx, item_3.ktx ... 
* cube: item_right.ktx, item_left.ktx, item_top.ktx ...


Examples:

`python ktxmerge.py --array --output <filename> --input <input files>`
`python ktxmerge.py --array --output foobar.ktx --input foo.ktx bar.ktx`

`python ktxmerge.py --array --auto --output <filename> --input <input wildcard>`
`python ktxmerge.py --array --auto --output foobar.ktx --input foo_*`

`python ktxmerge.py --cube --output <filename> --input <input files>`
`python ktxmerge.py --cube --output cube.ktx --input right.ktx left.ktx top.ktx bottom.ktx front.ktx back.ktx`

`python ktxmerge.py --cube --auto --output <filename> --input <input wildcard>`
`python ktxmerge.py --cube --auto --output cube.ktx --input foo_*`


"""

from ctypes import c_uint8, c_uint32, sizeof, Structure
from collections import namedtuple
from pathlib import Path
from io import BytesIO
from enum import Enum
import sys


KTX_ID = (c_uint8*12)(0xAB, 0x4B, 0x54, 0x58, 0x20, 0x31, 0x31, 0xBB, 0x0D, 0x0A, 0x1A, 0x0A)
MipmapData = namedtuple('MipmapData', ('index', 'layer', 'face', 'offset', 'size', 'width', 'height'))


class KTXException(Exception):
    pass


class KtxHeader(Structure):
    """
    The header of a ktx file
    """
    _fields_ = (
        ('id', c_uint8*12),
        ('endianness', c_uint32),
        ('gl_type', c_uint32),
        ('gl_type_size', c_uint32),
        ('gl_format', c_uint32),
        ('gl_internal_format', c_uint32),
        ('gl_base_internal_format', c_uint32),
        ('pixel_width', c_uint32),
        ('pixel_height', c_uint32),
        ('pixel_depth', c_uint32),
        ('number_of_array_elements', c_uint32),
        ('number_of_faces', c_uint32),
        ('number_of_mipmap_levels', c_uint32),
        ('bytes_of_key_value_data', c_uint32),
    )

    def __repr__(self):
        fields = {}
        for name, _ in self._fields_:
            value = getattr(self, name)
            fields[name] = value
                
        return f"KtxHeader({repr(fields)})"


class KTXFile(object):

    def __init__(self, fname, header, data):
        self.file_name = fname

        self.width = header.pixel_width
        self.height = max(header.pixel_height, 1)
        self.depth = max(header.pixel_depth, 1)
        self.mips_level = max(header.number_of_mipmap_levels, 1)
        self.array_element = max(header.number_of_array_elements, 1)
        self.faces = max(header.number_of_faces, 1)
        self.header = header

        if header.endianness != 0x04030201:
            raise ValueError("The endianess of this file do not match your system")

        if not self.compressed:
            raise ValueError("This tool only works with compressed file format")

        self.data = data
        self.mipmaps = []

        data_offset = 0
        mip_extent_width, mip_extent_height = self.width, self.height

        for mipmap_index in range(self.mips_level):
            mipmap_size_bytes = data[data_offset:4].cast("I")[0]
            data_offset += 4

            for layer_index in range(self.array_element):
                for face_index in range(self.faces):
                    mipmap = MipmapData(mipmap_index, layer_index, face_index, data_offset, mipmap_size_bytes, mip_extent_width, mip_extent_height)
                    self.mipmaps.append(mipmap)

                    data_offset += mipmap_size_bytes

            mip_extent_width //= 2
            mip_extent_height //= 2

    @staticmethod
    def merge_array(*inputs):
        header = { key: None for key, _ in KtxHeader._fields_ }
        header["number_of_faces"] = 1
        header["number_of_array_elements"] = len(inputs)
        header["pixel_depth"] = 0
        header["endianness"] = 0x04030201

        # Read and validate the files
        files = []
        for i in inputs:
            f = KTXFile.open(i)
            
            if f.array_element > 1:
                raise KTXException(f"File {f.file_name} is already a texture array")
            
            if f.faces > 1:
                raise KTXException(f"File {f.file_name} is a cubemap")

            check_mismatch(header, "pixel_width", f.width)
            check_mismatch(header, "pixel_height", f.width)
            check_mismatch(header, "number_of_mipmap_levels", f.mips_level)
            check_mismatch(header, "gl_type", f.header.gl_type)
            check_mismatch(header, "gl_type_size", f.header.gl_type_size)
            check_mismatch(header, "gl_format", f.header.gl_format)
            check_mismatch(header, "gl_internal_format", f.header.gl_internal_format)
            check_mismatch(header, "gl_base_internal_format", f.header.gl_base_internal_format)

            files.append(f)

        # Build the final headers
        header_filtered = {k:v for k,v in header.items() if v is not None}
        header = KtxHeader(**header_filtered)
        header.id[::] = KTX_ID

        # Write data
        data = BytesIO()
        for mipmap_level in range(header.number_of_mipmap_levels):
            for array_layer, file in enumerate(files):
                mipmap = file.find_mipmap(mipmap_level)
                data.write(c_uint32(mipmap.size))
                data.write(file.mipmap_data(mipmap))

        return KTXFile("output.ktx", header, memoryview(data.getvalue()))

    @staticmethod
    def merge_cube(*inputs):
        files = []
        header = KtxHeader()
        header.number_of_faces = 6
        header.endianness = 0x04030201
        data = bytearray()

        if len(inputs) != 6:
            raise KTXException(f"A cubemap must have 6 inputs, {len(inputs)} given.")

        for i in inputs:
            f = KTXFile.open(i)
        
        return KTXFile("output.ktx", header, data)

    @staticmethod
    def open(path):
        """
        Load and parse a KTX texture

        :param path: The relative path of the file to load
        :return: A KTXFile texture object
        """
        header_size = sizeof(KtxHeader)
        data = length = None
        with Path(path).open('rb') as f:
            data = memoryview(f.read())
            length = len(data)

        # File size check
        if length < header_size:
            msg = "The file ID is invalid: length inferior to the ktx header"
            raise IOError(msg.format(path))

        # Header check
        header = KtxHeader.from_buffer_copy(data[0:header_size])
        if header.id[::] != KTX_ID[::]:
            msg = "The file ID is invalid: header do not match the ktx header"
            raise IOError(msg.format(path))

        offset = sizeof(KtxHeader) + header.bytes_of_key_value_data
        texture = KTXFile(path, header, data[offset::])

        return texture

    @property
    def compressed(self):
        return self.header.gl_format == 0

    def find_mipmap(self, index, layer=0, face=0):
        for m in self.mipmaps:
            if m.index == index  and m.layer == layer and m.face == face:
                return m

        raise IndexError(f"No mipmap found with the following attributes: index={index}, layer={layer}, face={face}")

    def mipmap_data(self, mipmap):
        offset = mipmap.offset
        size = mipmap.size

        return self.data[offset:offset+size]

    def save(self, outfile):
        outfile.write(self.header)
        outfile.write(self.data)


def check_mismatch(obj, member, value):
    obj_value = obj.get(member, None)
    if obj_value is None:
        obj[member] = value
    elif obj_value != value:
        raise KTXException(f"Property mismatch for \"{member}\": Expected: \"{obj_value}\" / Actual: \"{value}\"")


def auto_input(pattern, cube):
    if cube:
        pass
    else:
        return tuple(Path('.').glob(pattern+'.ktx'))

if __name__ == "__main__":
    try:
        argv = sys.argv
        array = "--array" in argv
        cube = "--cube" in argv
        auto = "--auto" in argv

        if auto:
            inputs = auto_input(argv[argv.index("--input")+1], cube) 
        else:
            inputs = argv[argv.index("--input")+1::]

        if array:
            out_file = KTXFile.merge_array(*inputs)
        elif cube:
            out_file = KTXFile.merge_cube(*inputs)

        output = argv[argv.index("--output")+1]

        with open(output, 'wb') as out:
            out_file.save(out)

    except KTXException as e:
        print(f"ERROR: {e}")
    except DeprecationWarning:
        print(__doc__)
