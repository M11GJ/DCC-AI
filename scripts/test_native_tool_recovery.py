import asyncio,json,sys,pathlib,unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import dccai_pipe as p
from unittest.mock import AsyncMock

TOOLS=[{'type':'function','function':{'name':'search_web','parameters':{'type':'object','properties':{'query':{'type':'string'}},'required':['query']}}}]
PAYLOAD={'tools':TOOLS,'messages':[{'role':'user','content':'検索してください'}],'stream':True}
XML='調べます。<tool_calls><invoke name="search_web"><parameter name="query">DCC</parameter></invoke></tool_calls>'
class Response:
 def __init__(self,data):self.data=data
 def raise_for_status(self):pass
 def json(self):return self.data

def native(name='search_web'):
 return {'choices':[{'index':0,'message':{'role':'assistant','content':None,'reasoning_content':'original-provider-reasoning','tool_calls':[{'id':'call1','type':'function','function':{'name':name,'arguments':'{"query":"DCC"}'}}]},'finish_reason':'tool_calls'}],'usage':{'total_tokens':4}}
class Tests(unittest.IsolatedAsyncioTestCase):
 async def test_prose_xml_regenerates_native_with_real_ids(self):
  client=AsyncMock();client.post.return_value=Response(native())
  r=await p._repair_native_tool_response({'choices':[{'message':{'content':XML}}],'usage':{'total_tokens':2}},PAYLOAD,client,'url',{})
  self.assertEqual(r['choices'][0]['message']['tool_calls'][0]['id'],'call1')
  self.assertEqual(r['usage']['total_tokens'],6)
  self.assertEqual(client.post.await_count,1)
 async def test_unknown_tool_and_second_malformed_fail_closed(self):
  for r in (native('delete_everything'),{'choices':[{'message':{'content':XML}}]}):
   client=AsyncMock();client.post.return_value=Response(r)
   with self.assertRaises(ValueError):await p._repair_native_tool_response({'choices':[{'message':{'content':XML}}]},PAYLOAD,client,'url',{})
   self.assertEqual(client.post.await_count,1)
 async def test_code_example_and_disabled_tools_are_not_executed(self):
  client=AsyncMock()
  for text,body in [('```xml\n'+XML+'\n```',PAYLOAD),(XML,{**PAYLOAD,'tool_choice':'none'}),(XML,{'messages':[]})]:
   original={'choices':[{'message':{'content':text}}]}
   self.assertIs(await p._repair_native_tool_response(original,body,client,'url',{}),original)
  client.post.assert_not_called()
 async def test_stream_split_markup_repaired_no_leak_reasoning_preserved(self):
  async def lines():
   yield 'data: '+json.dumps({'choices':[{'delta':{'reasoning_content':'FAILED_REASONING'}}]})
   for t in [XML[:12],XML[12:45],XML[45:]]:
    yield 'data: '+json.dumps({'choices':[{'delta':{'content':t}}]})
   yield 'data: [DONE]'
  client=AsyncMock();client.post.return_value=Response(native())
  chunks=[x async for x in p._native_tool_stream(lines(),PAYLOAD,client,'url',{})]
  joined=''.join(chunks)
  self.assertNotIn('<tool_calls>',joined);self.assertNotIn('FAILED_REASONING',joined)
  self.assertIn('original-provider-reasoning',joined)
  self.assertEqual(json.loads(chunks[0][6:])['choices'][0]['delta']['tool_calls'][0]['index'],0)
 async def test_native_stream_passes_without_retry(self):
  chunks=['data: '+json.dumps({'choices':[{'delta':native()['choices'][0]['message'],'finish_reason':'tool_calls'}]}),'data: [DONE]']
  async def lines():
   for c in chunks:yield c
  client=AsyncMock();out=[x async for x in p._native_tool_stream(lines(),PAYLOAD,client,'url',{})]
  self.assertIn('call1',''.join(out));client.post.assert_not_called()
if __name__=='__main__':unittest.main()
