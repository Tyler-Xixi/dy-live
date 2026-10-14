import stat,struct,tempfile,unittest,zipfile
from pathlib import Path
from tools.normalize_mac_zip import normalize

class MacZipTests(unittest.TestCase):
 def test_unmarked_utf8_is_fixed_without_changing_signed_payload_or_permissions(self):
  with tempfile.TemporaryDirectory() as d:
   source=Path(d)/'old.zip';target=Path(d)/'new.zip'
   entries={'中文.app/Contents/MacOS/bin':b'signed executable','中文.app/Contents/Resources/link':b'../Frameworks/lib.dylib'}
   with zipfile.ZipFile(source,'w') as z:
    for name,data in entries.items():
     info=zipfile.ZipInfo(name);info.create_system=3
     info.external_attr=((stat.S_IFLNK if name.endswith('link') else stat.S_IFREG)|0o755)<<16
     z.writestr(info,data)
   data=bytearray(source.read_bytes())
   for marker,offset in ((b'PK\x03\x04',6),(b'PK\x01\x02',8)):
    position=0
    while (position:=data.find(marker,position))!=-1:
     flags=struct.unpack_from('<H',data,position+offset)[0]
     struct.pack_into('<H',data,position+offset,flags&~0x800);position+=4
   source.write_bytes(data)
   normalize(source,target)
   with zipfile.ZipFile(target) as z,zipfile.ZipFile(source,metadata_encoding='utf-8') as old:
    self.assertIn('README.txt',z.namelist())
    for name,expected in entries.items():
     self.assertEqual(z.read(name),expected)
     self.assertTrue(z.getinfo(name).flag_bits&0x800)
     self.assertEqual(z.getinfo(name).external_attr,old.getinfo(name).external_attr)
    self.assertIsNone(z.testzip())

if __name__=='__main__':unittest.main()
