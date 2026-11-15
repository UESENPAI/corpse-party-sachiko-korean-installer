"""Headless Linux font masks through the system FreeType library.

Uses FreeType's public structures and API via ctypes, with no pip dependency.
The supplied OFL TTF is kept in this process and never installed system-wide.
Public layout reference: https://freetype.org/freetype2/docs/reference/ .
"""
import ctypes as C
import ctypes.util
import os
from pathlib import Path


class Generic(C.Structure):
    _fields_ = [('data', C.c_void_p), ('finalizer', C.c_void_p)]


class Vector(C.Structure):
    _fields_ = [('x', C.c_long), ('y', C.c_long)]


class Box(C.Structure):
    _fields_ = [('x_min', C.c_long), ('y_min', C.c_long), ('x_max', C.c_long), ('y_max', C.c_long)]


class Bitmap(C.Structure):
    _fields_ = [('rows', C.c_uint), ('width', C.c_uint), ('pitch', C.c_int),
                ('buffer', C.c_void_p), ('num_grays', C.c_ushort),
                ('pixel_mode', C.c_ubyte), ('palette_mode', C.c_ubyte), ('palette', C.c_void_p)]


class Metrics(C.Structure):
    _fields_ = [(name, C.c_long) for name in ('width', 'height', 'hori_bearing_x', 'hori_bearing_y',
                                            'hori_advance', 'vert_bearing_x', 'vert_bearing_y', 'vert_advance')]


class GlyphSlot(C.Structure):
    _fields_ = [('library', C.c_void_p), ('face', C.c_void_p), ('next', C.c_void_p),
                ('glyph_index', C.c_uint), ('generic', Generic), ('metrics', Metrics),
                ('linear_hori_advance', C.c_long), ('linear_vert_advance', C.c_long),
                ('advance', Vector), ('format', C.c_uint), ('bitmap', Bitmap),
                ('bitmap_left', C.c_int), ('bitmap_top', C.c_int)]


class Face(C.Structure):
    _fields_ = [(name, C.c_long) for name in ('num_faces', 'face_index', 'face_flags', 'style_flags', 'num_glyphs')] + [
        ('family_name', C.c_char_p), ('style_name', C.c_char_p),
        ('num_fixed_sizes', C.c_int), ('available_sizes', C.c_void_p),
        ('num_charmaps', C.c_int), ('charmaps', C.c_void_p), ('generic', Generic), ('bbox', Box),
        ('units_per_em', C.c_ushort), ('ascender', C.c_short), ('descender', C.c_short),
        ('height', C.c_short), ('max_advance_width', C.c_short), ('max_advance_height', C.c_short),
        ('underline_position', C.c_short), ('underline_thickness', C.c_short),
        ('glyph', C.POINTER(GlyphSlot)), ('size', C.c_void_p), ('charmap', C.c_void_p)]


class PrivateFont:
    def __init__(self, path, family='Noto Sans KR'):
        if os.name != 'posix':
            raise RuntimeError('This FreeType backend requires a POSIX platform')
        path = Path(path)
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 64 * 1024 * 1024:
            raise ValueError('Provide a regular Noto Sans KR TTF file')
        library = ctypes.util.find_library('freetype')
        if not library:
            raise RuntimeError('The system FreeType library is required')
        self.api = C.CDLL(library)
        signatures = {
            'FT_Init_FreeType': ([C.POINTER(C.c_void_p)], C.c_int),
            'FT_Done_FreeType': ([C.c_void_p], C.c_int),
            'FT_New_Memory_Face': ([C.c_void_p, C.c_void_p, C.c_long, C.c_long, C.POINTER(C.POINTER(Face))], C.c_int),
            'FT_Done_Face': ([C.POINTER(Face)], C.c_int),
            'FT_Select_Charmap': ([C.POINTER(Face), C.c_uint], C.c_int),
            'FT_Set_Pixel_Sizes': ([C.POINTER(Face), C.c_uint, C.c_uint], C.c_int),
            'FT_Get_Char_Index': ([C.POINTER(Face), C.c_ulong], C.c_uint),
            'FT_Load_Char': ([C.POINTER(Face), C.c_ulong, C.c_int32], C.c_int),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = arguments, result
        self.library = C.c_void_p()
        self.face = C.POINTER(Face)()
        self.size = None
        self.verified_characters = set()
        self.font_buffer = C.create_string_buffer(path.read_bytes())
        try:
            if self.api.FT_Init_FreeType(C.byref(self.library)):
                raise OSError('FreeType initialization failed')
            if self.api.FT_New_Memory_Face(self.library, self.font_buffer, len(self.font_buffer)-1, 0, C.byref(self.face)):
                raise OSError('Private font loading failed')
            name = (self.face.contents.family_name or b'').decode('utf-8', 'replace')
            if name != family:
                raise ValueError('Unexpected font family: ' + name)
            if self.api.FT_Select_Charmap(self.face, 0x756e6963):
                raise ValueError('The font has no Unicode character map')
            # Noto Sans KR's weight axis; static fonts may reject this option.
            variation = getattr(self.api, 'FT_Set_Var_Design_Coordinates', None)
            if variation:
                variation.argtypes = [C.POINTER(Face), C.c_uint, C.POINTER(C.c_long)]
                variation.restype = C.c_int
                weight = C.c_long(600 * 65536)
                variation(self.face, 1, C.byref(weight))
        except BaseException:
            self.close()
            raise

    def raster(self, character, size):
        if len(character) != 1 or not 0 < ord(character) <= 0xffff or not 1 <= size <= 512:
            raise ValueError('Expected a BMP character and supported pixel size')
        if size != self.size:
            if self.api.FT_Set_Pixel_Sizes(self.face, 0, size):
                raise OSError('FreeType pixel sizing failed')
            self.size = size
        if character not in self.verified_characters:
            if not self.api.FT_Get_Char_Index(self.face, ord(character)):
                raise ValueError('The supplied font is missing a required character')
            self.verified_characters.add(character)
        # Grid fitting can collapse a complex Hangul outline to zero coverage
        # at one-pixel text sizes. Keep its native antialiased outline instead.
        load_flags = 4 | (2 if size < 8 else 0)
        if self.api.FT_Load_Char(self.face, ord(character), load_flags):
            raise OSError('FreeType glyph rendering failed')
        bitmap = self.face.contents.glyph.contents.bitmap
        if not bitmap.width or not bitmap.rows:
            return 1, 1, b'\0'
        if (bitmap.pixel_mode != 2 or bitmap.num_grays != 256 or not bitmap.buffer
                or bitmap.width > 2048 or bitmap.rows > 2048 or abs(bitmap.pitch) < bitmap.width):
            raise ValueError('Unsupported FreeType grayscale bitmap')
        pitch = bitmap.pitch
        rows = [C.string_at(bitmap.buffer + y * pitch, bitmap.width) for y in range(bitmap.rows)]
        return bitmap.width, bitmap.rows, b''.join(rows)

    def close(self):
        if self.face:
            self.api.FT_Done_Face(self.face)
            self.face = C.POINTER(Face)()
        if self.library:
            self.api.FT_Done_FreeType(self.library)
            self.library = C.c_void_p()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
