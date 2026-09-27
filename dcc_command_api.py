from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from open_webui.models.chats import Chats
from open_webui.utils.auth import get_verified_user
from open_webui.tasks import has_active_tasks
from starlette.requests import Request
from dcc_modes import MODES, enforce_mode
router=APIRouter()
class ModeForm(BaseModel):
 chat_id:str
 mode:str
async def owned(chat_id,user):
 chat=await Chats.get_chat_by_id_and_user_id(chat_id,user.id)
 if not chat:raise HTTPException(404,'会話が見つかりません')
 return chat
@router.get('/mode')
async def get_mode(chat_id:str,user=Depends(get_verified_user)):
 chat=await owned(chat_id,user)
 return {'mode':(chat.chat or {}).get('dcc_mode','normal')}
@router.post('/mode')
async def set_mode(request:Request,form:ModeForm,user=Depends(get_verified_user)):
 if form.mode not in MODES:raise HTTPException(400,'無効なモードです')
 await owned(form.chat_id,user)
 if await has_active_tasks(request.app.state.redis,form.chat_id):raise HTTPException(409,'生成が終わってからモードを切り替えてください')
 await Chats.update_chat_by_id(form.chat_id,{'dcc_mode':form.mode,'dcc_plan_status':None,'dcc_plan_message_id':None},touch=False)
 return {'mode':form.mode}
async def prepare_mode(form_data,metadata,user):
 requested=metadata.get('dcc_requested_mode')
 if requested is not None and requested not in MODES:raise HTTPException(400,'無効なモードです')
 cid=metadata.get('chat_id')
 chat=await Chats.get_chat_by_id_and_user_id(cid,user.id) if cid and not cid.startswith(('local:','channel:')) else None
 data=(chat.chat or {}) if chat else {}
 mode=requested if requested is not None else data.get('dcc_mode','normal')
 if chat and data.get('dcc_native_plan_call_id') and metadata.get('assistant_message_id')==data.get('dcc_native_plan_message_id'):
  message=await Chats.get_message_by_id_and_message_id(cid,data['dcc_native_plan_message_id'])
  mode=resolve_native_decision((message or {}).get('output',[]),data['dcc_native_plan_call_id'])
  if mode is None:mode='plan'
  else:await Chats.update_chat_by_id(cid,{'dcc_native_plan_call_id':None,'dcc_native_plan_message_id':None},touch=False)
 if chat:await Chats.update_chat_by_id(cid,{'dcc_mode':mode if mode in MODES else 'normal'},touch=False)
 metadata['dcc_mode']=mode
 if mode!='normal':
  # Keep pure question tools available, but do not run legacy retrieval.
  form_data.setdefault('params',{})['function_calling']='native'
  metadata.setdefault('params',{})['function_calling']='native'
 return form_data,metadata

def resolve_native_decision(output,call_id):
 import json
 for item in output:
  if item.get('type')!='function_call_output' or item.get('call_id')!=call_id:continue
  raw=item.get('output')
  if isinstance(raw,list):raw=''.join(p.get('text','') for p in raw if isinstance(p,dict))
  try:result=json.loads(raw) if isinstance(raw,str) else raw
  except (ValueError,TypeError):return 'cancelled'
  if not isinstance(result,dict) or result.get('status')!='answered':return 'cancelled'
  answer=(result.get('answers') or {}).get('dcc_plan_decision') or {}
  if answer.get('type')=='option':return {0:'normal',1:'plan',2:'cancelled'}.get(answer.get('option_index'),'cancelled')
  return 'plan' # Free text means revision, never permission to execute.
 return None

def restrict_mode_tools(form_data,metadata):
 mode=metadata.get('dcc_mode','normal')
 if mode=='normal':return form_data
 restricted=enforce_mode(form_data,mode)
 form_data={**form_data,'tools':restricted['tools'],'tool_choice':restricted['tool_choice']}
 allowed={'ask_user'} if mode in ('plan','grill-me') else set()
 metadata['tools']={name:tool for name,tool in (metadata.get('tools') or {}).items() if name in allowed}
 return form_data

def native_plan_question():
 import json,uuid
 return {'id':'call_plan_'+uuid.uuid4().hex,'type':'function','function':{'name':'ask_user','arguments':json.dumps({'questions':[{'id':'dcc_plan_decision','header':'次の進め方','question':'計画を作成しました。次はどうしますか？','options':[{'label':'この計画を実行','description':'提示された計画に沿って作業を始めます。'},{'label':'計画を修正','description':'修正したい条件や内容を伝えて、計画を見直します。'},{'label':'実行しない','description':'作業を始めず、ここで終了します。'}]}],'allow_other':True},ensure_ascii=False)}}

@router.post('/plan-decision')
async def retired_plan_decision():
 raise HTTPException(410,'純正の質問欄から回答してください。画面を再読み込みしてください。')

async def finish_mode(metadata,user,success=True,request=None,form_data=None):
 if metadata.get('dcc_mode')!='plan':return
 cid=metadata.get('chat_id');mid=metadata.get('message_id')
 chat=await Chats.get_chat_by_id_and_user_id(cid,user.id)
 if not chat:return
 data=chat.chat or {};current=getattr(chat,'current_message_id',None) or (data.get('history') or {}).get('currentId')
 if current!=mid:return
 message=await Chats.get_message_by_id_and_message_id(cid,mid)
 content=(message or {}).get('content') or ''
 output=(message or {}).get('output') or []
 # A model clarification question is already staged: do not duplicate it.
 if any(i.get('type')=='function_call' and i.get('name')=='ask_user' and i.get('status')=='pending' for i in output):return
 valid=success and bool(content.strip()) and not (message or {}).get('error') and '問い合わせ番号：DCC-' not in content
 update={'dcc_mode':'normal','dcc_plan_status':None,'dcc_plan_message_id':None}
 if valid:
  from open_webui.utils.ask_user import stage_ask_user_tool_calls
  from open_webui.utils.middleware import pause_for_tool_approval
  from open_webui.socket.main import get_event_emitter
  import uuid
  call=native_plan_question()
  staged,error=stage_ask_user_tool_calls([call],output,lambda prefix:prefix+'_'+uuid.uuid4().hex)
  if error or not staged:raise RuntimeError('Native question staging failed')
  await pause_for_tool_approval(cid,mid,output,form_data or {},metadata)
  update.update(dcc_native_plan_call_id=call['id'],dcc_native_plan_message_id=mid)
  emitter=await get_event_emitter(metadata)
  if emitter:await emitter({'type':'chat:completion','data':{'done':False,'output':output}})
 await Chats.update_chat_by_id(cid,update,touch=False)
