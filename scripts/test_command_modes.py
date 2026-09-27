import sys,pathlib,unittest,copy
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from dcc_modes import enforce_mode
class Tests(unittest.TestCase):
 def test_modes_block_tools_without_modifying_user_text(self):
  payload={'messages':[{'role':'user','content':'Create a file'}],'tools':[{'type':'function','function':{'name':'write_file'}}],'tool_choice':'required'};before=copy.deepcopy(payload)
  for mode in ('plan','grill-me'):
   result=enforce_mode(payload,mode);self.assertEqual(result['tools'],[]);self.assertEqual(result['tool_choice'],'none');self.assertEqual(result['messages'][1:],payload['messages'])
  self.assertEqual(payload,before)
 def test_question_mode_allows_only_question_tool(self):
  p={'tools':[{'type':'function','function':{'name':name}} for name in ('ask_user','write_file','search_web')],'messages':[]}
  result=enforce_mode(p,'grill-me');self.assertEqual([t['function']['name'] for t in result['tools']],['ask_user']);self.assertEqual(result['tool_choice'],'auto')
 def test_normal_mode_keeps_tools(self):
  payload={'tools':['test']};self.assertIs(enforce_mode(payload,'normal'),payload)
if __name__=='__main__':unittest.main()
