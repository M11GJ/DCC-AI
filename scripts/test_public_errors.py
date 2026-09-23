import json,os,pathlib,sys,tempfile,unittest
from unittest import mock
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import dccai_errors as e

class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=pathlib.Path(self.tmp.name)/'errors.jsonl'
  self.env=mock.patch.dict(os.environ,{'DCCAI_ERROR_LOG':str(self.path)});self.env.start()
  self.logs=mock.patch('dccai_errors.logging.getLogger');self.logs.start()
 def tearDown(self):self.logs.stop();self.env.stop();self.tmp.cleanup()
 def test_cause_before_status_and_fallback_wrapper(self):
  for detail,status,code in [
   ('unsupported image. No fallback model group found',400,'unsupported_image'),
   ('context_length_exceeded',400,'context_limit'),('insufficient_quota',429,'quota_exceeded'),
   ('CAPTCHA',429,'search_limited'),('ReadTimeout',None,'timeout'),('reasoning_content missing',400,'tool_error'),
   ('unknown',401,'authentication_error'),('unknown',413,'payload_too_large'),('unknown',503,'service_error'),('unknown',None,'internal_error')]:
   with self.subTest(code=code):self.assertEqual(e.classify_error(detail,status),code)
 def test_public_output_hides_details_but_log_is_correlated(self):
  detail='unsupported image /internal/model No fallback model group found '+('x'*4000)
  r=e.public_error(detail,400,'test')
  self.assertLess(len(r['message']),180);self.assertNotIn('fallback',r['message'])
  log=json.loads(self.path.read_text());self.assertEqual(log['request_id'],r['request_id']);self.assertEqual(log['detail'],detail)
  self.assertEqual(self.path.stat().st_mode & 0o777,0o600)
 def test_secrets_redacted_and_ids_unique(self):
  r=e.public_error('Authorization: Bearer TOPSECRET api_key=PRIVATE password=SECRET data:image/png;base64,YWJjZA== sk-abcdef',400)
  logged=self.path.read_text()
  for secret in ['TOPSECRET','PRIVATE','SECRET data','YWJjZA==','sk-abcdef']:self.assertNotIn(secret,logged)
  self.assertNotEqual(r['request_id'],e.public_error('other')['request_id'])
 def test_http_error_classifies_response_body(self):
  import httpx
  r=httpx.Response(400,text='unsupported image',request=httpx.Request('POST','http://internal'))
  exc=httpx.HTTPStatusError('opaque',request=r.request,response=r)
  self.assertEqual(e.public_error(exc)['code'],'unsupported_image')
 def test_pipe_stream_and_nonstream_hide_provider_body(self):
  import asyncio,httpx,dccai_pipe
  detail='unsupported image No fallback model group found INTERNAL_SENTINEL'
  response=httpx.Response(400,text=detail,request=httpx.Request('POST','http://upstream'))
  class Context:
   async def __aenter__(self):return response
   async def __aexit__(self,*args):return False
  class Client(Context):
   def __init__(self,*args,**kwargs):pass
   async def __aenter__(self):return self
   async def post(self,*args,**kwargs):return response
   def stream(self,*args,**kwargs):return Context()
  async def scenario():
   pipe=dccai_pipe.Pipe();pipe.valves.LITELLM_API_KEY='test';pipe.valves.MONTHLY_TOKEN_LIMIT=0
   for streaming in [False,True]:
    r=await pipe.pipe({'model':'dccai.dccai-high-vision','messages':[{'role':'user','content':'test'}],'stream':streaming},__metadata__={'chat_id':'test'})
    answer=''.join([s async for s in r]) if streaming else r
    self.assertIn('画像を読み込めません',answer);self.assertIn('DCC-',answer);self.assertNotIn('INTERNAL_SENTINEL',answer)
  with mock.patch.object(dccai_pipe.httpx,'AsyncClient',Client):asyncio.run(scenario())
 def test_responses_both_modes_keep_status_and_hide_raw_error(self):
  import httpx
  from scripts import responses_gateway as g
  response=httpx.Response(400,text='unsupported image INTERNAL_SENTINEL',request=httpx.Request('POST','http://upstream'))
  for streaming in [False,True]:
   handler=object.__new__(g.Handler);handler._send_json=mock.Mock()
   client=mock.MagicMock();client.__enter__.return_value.post.return_value=response
   with mock.patch.object(g.httpx,'Client',return_value=client):handler._proxy({'model':'dccai-high'},streaming,'test')
   status,body=handler._send_json.call_args.args
   self.assertEqual(status,400);self.assertEqual(body['error']['code'],'unsupported_image')
   self.assertNotIn('INTERNAL_SENTINEL',json.dumps(body));self.assertIn('DCC-',body['error']['request_id'])
 def test_existing_structured_error_keeps_id(self):
  r=e.public_error('unknown');self.assertIs(e.public_error(r),r)
if __name__=='__main__':unittest.main()
