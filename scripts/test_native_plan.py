import ast,json,unittest
from pathlib import Path
s=Path(__file__).resolve().parents[1]/'dcc_command_api.py'
nodes=[n for n in ast.parse(s.read_text()).body if isinstance(n,ast.FunctionDef) and n.name in ('resolve_native_decision','native_plan_question')]
ns={};exec(compile(ast.Module(body=nodes,type_ignores=[]),'native_plan','exec'),ns)
class Tests(unittest.TestCase):
 def output(self,result):return [{'type':'function_call_output','call_id':'c','output':[{'type':'input_text','text':json.dumps(result)}]}]
 def test_only_explicit_execute_unblocks(self):
  for index,mode in [(0,'normal'),(1,'plan'),(2,'cancelled')]:
   result={'status':'answered','answers':{'dcc_plan_decision':{'type':'option','option_index':index}}}
   self.assertEqual(ns['resolve_native_decision'](self.output(result),'c'),mode)
 def test_cancel_and_free_text_do_not_execute(self):
  self.assertEqual(ns['resolve_native_decision'](self.output({'status':'cancelled'}),'c'),'cancelled')
  self.assertEqual(ns['resolve_native_decision'](self.output({'status':'answered','answers':{'dcc_plan_decision':{'type':'text','text':'変更して'}}}),'c'),'plan')
  self.assertIsNone(ns['resolve_native_decision']([], 'c'))
 def test_native_question_schema(self):
  call=ns['native_plan_question']();self.assertEqual(call['function']['name'],'ask_user')
  args=json.loads(call['function']['arguments']);self.assertTrue(args['allow_other']);self.assertEqual(len(args['questions'][0]['options']),3)
if __name__=='__main__':unittest.main()
