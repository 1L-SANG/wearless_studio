"""Regenerate Latin-only template metrics from bundled fonts using local FreeType.
Run from the repository root. No font download, browser or model calls.
"""
import ctypes as C,json,struct,hashlib,math
from ctypes.util import find_library
from pathlib import Path
ft=C.CDLL(find_library('freetype'))
ft.FT_Init_FreeType.argtypes=[C.POINTER(C.c_void_p)]
ft.FT_New_Face.argtypes=[C.c_void_p,C.c_char_p,C.c_long,C.POINTER(C.c_void_p)]
ft.FT_Load_Sfnt_Table.argtypes=[C.c_void_p,C.c_ulong,C.c_long,C.c_void_p,C.POINTER(C.c_ulong)]
ft.FT_Get_Char_Index.argtypes=[C.c_void_p,C.c_ulong];ft.FT_Get_Char_Index.restype=C.c_uint
ft.FT_Get_Advance.argtypes=[C.c_void_p,C.c_uint,C.c_int32,C.POINTER(C.c_long)]
ft.FT_Set_Var_Design_Coordinates.argtypes=[C.c_void_p,C.c_uint,C.POINTER(C.c_long)]
lib=C.c_void_p();assert ft.FT_Init_FreeType(C.byref(lib))==0
fonts={
 'Pretendard':'src/fonts/PretendardVariable.woff2',
 'Cormorant':'src/fonts/Cormorant-VariableFont_wght.woff2',
 'Cormorant:italic':'src/fonts/CormorantGaramond-Italic-500-latin.woff2',
}
result={'description':'Conservative Latin advances from the bundled fonts (FreeType FT_Get_Advance, unscaled, no kerning). Max over used weights; normalized upward to 1000 units. Korean/general body metrics are unchanged.', 'unitsPerEm':1000,'fonts':{}}
for key,path in fonts.items():
 face=C.c_void_p();assert ft.FT_New_Face(lib,str(Path(path).resolve()).encode(),0,C.byref(face))==0
 n=C.c_ulong();tag=int.from_bytes(b'head','big');assert ft.FT_Load_Sfnt_Table(face,tag,0,None,C.byref(n))==0
 data=C.create_string_buffer(n.value);assert ft.FT_Load_Sfnt_Table(face,tag,0,data,C.byref(n))==0
 em=struct.unpack_from('>H',data.raw,18)[0]
 widths={chr(i):0 for i in range(32,127)}
 weights=[300,400,500,600,700,800,900] if key=='Pretendard' else [400,500,600,700] if key=='Cormorant' else [500]
 for weight in weights:
  if key!='Cormorant:italic':assert ft.FT_Set_Var_Design_Coordinates(face,1,(C.c_long*1)(weight*65536))==0
  for char in widths:
   glyph=ft.FT_Get_Char_Index(face,ord(char));advance=C.c_long();assert ft.FT_Get_Advance(face,glyph,1,C.byref(advance))==0
   widths[char]=max(widths[char],math.ceil(advance.value*1000/em))
 result['fonts'][key]={'source':path,'sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest(),'weights':weights,'widths':widths}
 print(key,'BROWN units',sum(widths[c] for c in 'BROWN'),'at28+tracking3.36',sum(widths[c] for c in 'BROWN')*.028+5*3.36)
Path('server/app/data/page_template_latin_metrics.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
