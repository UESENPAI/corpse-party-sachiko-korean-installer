"""Headless Windows GDI masks from a private in-memory Noto Sans KR TTF.

No GUI windows or system font installation. The memory font is removed when
the context closes, including error paths. Uses only the Python standard lib.
"""
import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path


class FIXED(C.Structure):
    _fields_ = [('fract', W.WORD), ('value', C.c_short)]


class MAT2(C.Structure):
    _fields_ = [('eM11', FIXED), ('eM12', FIXED), ('eM21', FIXED), ('eM22', FIXED)]


class GLYPHMETRICS(C.Structure):
    _fields_ = [('width', W.UINT), ('height', W.UINT), ('origin', W.POINT),
                ('advance_x', C.c_short), ('advance_y', C.c_short)]


GRAY8_TO_ALPHA = bytes(min(255, round(value * 255 / 64)) for value in range(256))


class PrivateFont:
    def __init__(self, path, family='Noto Sans KR'):
        if os.name != 'nt':
            raise RuntimeError('Windows GDI font backend required; Linux backend is not available yet')
        path = Path(path)
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 64 * 1024 * 1024:
            raise ValueError('Provide a regular Noto Sans KR TTF font file')
        self.resource = self.dc = self.current = self.original = None
        self.family, self.size = family, None
        self.verified_characters = set()
        self.gdi = C.WinDLL('gdi32', use_last_error=True)
        signatures = {
            'AddFontMemResourceEx': ([C.c_void_p, W.DWORD, C.c_void_p, C.POINTER(W.DWORD)], W.HANDLE),
            'RemoveFontMemResourceEx': ([W.HANDLE], W.BOOL),
            'CreateCompatibleDC': ([W.HDC], W.HDC), 'DeleteDC': ([W.HDC], W.BOOL),
            'SelectObject': ([W.HDC, W.HANDLE], W.HANDLE), 'DeleteObject': ([W.HANDLE], W.BOOL),
            'CreateFontW': ([C.c_int] * 5 + [W.DWORD] * 8 + [W.LPCWSTR], W.HFONT),
            'GetTextFaceW': ([W.HDC, C.c_int, W.LPWSTR], C.c_int),
            'GetGlyphIndicesW': ([W.HDC, W.LPCWSTR, C.c_int, C.POINTER(W.WORD), W.DWORD], W.DWORD),
            'GetGlyphOutlineW': ([W.HDC, W.UINT, W.UINT, C.POINTER(GLYPHMETRICS),
                                 W.DWORD, C.c_void_p, C.POINTER(MAT2)], W.DWORD),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.gdi, name)
            function.argtypes, function.restype = arguments, result
        self.matrix = MAT2(FIXED(0, 1), FIXED(0, 0), FIXED(0, 0), FIXED(0, 1))
        self.font_buffer = C.create_string_buffer(path.read_bytes())
        try:
            count = W.DWORD()
            self.resource = self.gdi.AddFontMemResourceEx(self.font_buffer, len(self.font_buffer) - 1, None, C.byref(count))
            if not self.resource or not count.value:
                raise OSError('Private in-memory font loading failed')
            self.dc = self.gdi.CreateCompatibleDC(None)
            if not self.dc:
                raise OSError('GDI memory DC creation failed')
        except BaseException:
            self.close()
            raise

    def raster(self, character, size):
        if len(character) != 1 or not 0 < ord(character) <= 0xffff or not 1 <= size <= 512:
            raise ValueError('Expected one BMP character and supported pixel size')
        if size != self.size:
            handle = self.gdi.CreateFontW(-size, 0, 0, 0, 600, 0, 0, 0, 129, 0, 0, 4, 0, self.family)
            if not handle:
                raise OSError('GDI font creation failed')
            previous = self.gdi.SelectObject(self.dc, handle)
            if not previous or previous == C.c_void_p(-1).value:
                self.gdi.DeleteObject(handle)
                raise OSError('GDI font selection failed')
            if self.original is None:
                self.original = previous
            if self.current:
                self.gdi.DeleteObject(self.current)
            self.current, self.size = handle, size
            face = C.create_unicode_buffer(256)
            if not self.gdi.GetTextFaceW(self.dc, len(face), face) or face.value != self.family:
                raise OSError('Unexpected font fallback')
        if character not in self.verified_characters:
            index = W.WORD()
            found = self.gdi.GetGlyphIndicesW(self.dc, character, 1, C.byref(index), 1)
            if found == 0xffffffff or index.value == 0xffff:
                raise OSError('The supplied font is missing a required character')
            self.verified_characters.add(character)
        metrics = GLYPHMETRICS()
        length = self.gdi.GetGlyphOutlineW(self.dc, ord(character), 6, C.byref(metrics), 0, None, C.byref(self.matrix))
        if length == 0xffffffff or length > 4 * 1024 * 1024:
            raise OSError('Glyph rasterization failed')
        if not metrics.width or not metrics.height:
            return 1, 1, b'\0'
        buffer = C.create_string_buffer(length)
        result = self.gdi.GetGlyphOutlineW(self.dc, ord(character), 6, C.byref(metrics), length, buffer, C.byref(self.matrix))
        if result != length:
            raise OSError('Glyph rasterization changed size')
        pitch = (metrics.width + 3) & ~3
        if pitch * metrics.height > length:
            raise OSError('Invalid GDI bitmap pitch')
        raw = buffer.raw
        rows = b''.join(raw[y * pitch:y * pitch + metrics.width] for y in range(metrics.height))
        return metrics.width, metrics.height, rows.translate(GRAY8_TO_ALPHA)

    def close(self):
        if self.dc:
            if self.original:
                self.gdi.SelectObject(self.dc, self.original)
            if self.current:
                self.gdi.DeleteObject(self.current)
            self.gdi.DeleteDC(self.dc)
            self.dc = self.current = self.original = None
        if self.resource:
            self.gdi.RemoveFontMemResourceEx(self.resource)
            self.resource = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
