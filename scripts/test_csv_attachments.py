import asyncio
import pathlib
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import dccai_pipe as p

ID = '12345678-1234-1234-1234-123456789abc'
CSV = '商品,金額,備考\r\nりんご,120,"一行目\n二行目,引用"\r\nみかん,230,通常\r\n'
class CsvTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
    def attachment(self, name='test.csv', mime='text/csv', url=ID):
        return f'<attached_files>\n<file type="file" url="{url}" name="{name}" content_type="{mime}"/>\n</attached_files>\n合計は？'
    def test_encodings_preserve_all_cells_and_multiline(self):
        path = self.root / (ID + '_test.csv')
        for enc in ('utf-8', 'utf-8-sig', 'cp932', 'utf-16'):
            with self.subTest(enc=enc):
                path.write_bytes(CSV.encode(enc))
                text, images = p._read_attached_files(self.attachment(), self.tmp.name)
                self.assertIn(CSV, text)
                self.assertIn('test.csv', text)
                self.assertIn('合計は？', text)
                self.assertEqual(images, [])
    def test_api_url_and_uppercase_extension(self):
        (self.root / (ID + '_test.CSV')).write_text(CSV)
        text, images = p._read_attached_files(self.attachment('test.CSV', 'application/octet-stream', '/api/v1/files/'+ID+'/content'), self.tmp.name)
        self.assertIn('りんご,120', text)
        self.assertEqual(images, [])
    def test_missing_and_bad_encoding_and_large_are_explicit(self):
        with self.assertRaisesRegex(ValueError, '再添付'):
            p._read_attached_files(self.attachment(), self.tmp.name)
        path = self.root / (ID + '_test.csv')
        path.write_bytes(b'\x00\x01\x02')
        with self.assertRaisesRegex(ValueError, '文字コード'):
            p._read_attached_files(self.attachment(), self.tmp.name)
        path.write_bytes(b'a'*32)
        with mock.patch.object(p, 'CSV_MAX_BYTES', 16):
            with self.assertRaisesRegex(ValueError, '分割'):
                p._read_attached_files(self.attachment(), self.tmp.name)
    def test_image_preserved_other_document_not_image(self):
        path = self.root / (ID + '_test.png'); path.write_bytes(b'png-test')
        text, images = p._read_attached_files(self.attachment('test.png','image/png'), self.tmp.name)
        self.assertEqual(len(images),1); self.assertTrue(images[0].startswith('data:image/png;base64,'))
        path.unlink(); (self.root / (ID + '_test.pdf')).write_bytes(b'pdf-test')
        original = self.attachment('test.pdf','application/pdf')
        text, images = p._read_attached_files(original,self.tmp.name)
        self.assertEqual(text,original); self.assertEqual(images,[])
    def test_pipe_csv_is_text_in_real_upstream_payload(self):
        (self.root / (ID+'_test.csv')).write_bytes(CSV.encode('cp932'))
        pipe=p.Pipe(); pipe.valves.LITELLM_API_KEY="test-key"; pipe.valves.MONTHLY_TOKEN_LIMIT=0; pipe.valves.HIGH_DAILY_LIMIT=0
        response=mock.Mock(); response.status_code=200; response.json.return_value={'choices':[{'message':{'role':'assistant','content':'350'}}]}
        client=mock.AsyncMock(); client.post.return_value=response
        reader=p._read_attached_files
        with mock.patch.object(p,'_read_attached_files',side_effect=lambda text:reader(text,self.tmp.name)), mock.patch.object(p.httpx,'AsyncClient') as factory:
            factory.return_value.__aenter__.return_value=client
            asyncio.run(pipe.pipe({'model':'dccai.dccai-high-vision','messages':[{'role':'user','content':self.attachment()}],'stream':False},__user__={'id':'csv-test'},__metadata__={'chat_id':'csv-test'}))
        payload=client.post.call_args.kwargs['json']
        self.assertIn(CSV,payload['messages'][-1]['content'])
        self.assertNotIn('image_url',str(payload['messages']))

if __name__=='__main__': unittest.main()
