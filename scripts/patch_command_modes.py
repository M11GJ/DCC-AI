from pathlib import Path
import re,sys

def patch(root):
 root=Path(root);main=root/'backend/open_webui/main.py';s=main.read_text()
 if 'from dcc_command_api import' not in s:
  line='from dcc_command_api import router as dcc_commands_router, prepare_mode as dcc_prepare_mode, restrict_mode_tools as dcc_restrict_mode_tools, finish_mode as dcc_finish_mode\n'
  future='from __future__ import annotations\n'
  s=s.replace(future,future+line,1) if future in s else line+s
  anchor="app.include_router(utils.router, prefix='/api/v1/utils', tags=['utils'])"
  assert anchor in s;s=s.replace(anchor,anchor+"\napp.include_router(dcc_commands_router, prefix='/api/v1/dcc/commands', tags=['dcc_commands'])",1)
  anchor="metadata = {\n            'user_id': user.id,"
  assert anchor in s;s=s.replace(anchor,"metadata = {\n            'dcc_requested_mode': form_data.pop('dcc_mode', None),\n            'user_id': user.id,",1)
  anchor='            form_data, metadata, events = await process_chat_payload(request, form_data, user, metadata, model)'
  assert anchor in s;s=s.replace(anchor,'            form_data, metadata = await dcc_prepare_mode(form_data, metadata, user)\n'+anchor+'\n            form_data = dcc_restrict_mode_tools(form_data, metadata)',1)
  anchor='            return await process_chat_response(response, ctx)'
  assert anchor in s;s=s.replace(anchor,'            result = await process_chat_response(response, ctx)\n            await dcc_finish_mode(metadata, user, request=request, form_data=form_data)\n            return result',1)
  anchor="        except asyncio.CancelledError:\n            log.info('Chat processing was cancelled')"
  assert anchor in s;s=s.replace(anchor,"        except asyncio.CancelledError:\n            await asyncio.shield(dcc_finish_mode(metadata, user, False))\n            log.info('Chat processing was cancelled')",1)
  anchor="            error_detail = dcc_public_error(e, getattr(e, 'status_code', None), 'webui_request')['message']"
  assert anchor in s;s=s.replace(anchor,"            await dcc_finish_mode(metadata, user, False)\n"+anchor,1)
  compile(s,str(main),'exec');main.write_text(s)
 found=False
 for file in (root/'build/_app/immutable/chunks').glob('*.js'):
  s=file.read_text()
  anchor='if((lt==null?void 0:lt.type)==="prompt"){Qr(ft,Bt,lt.content??"");return}'
  if anchor not in s:continue
  found=True
  s='import {selectDccMode as dccSelectMode} from "/dcc-native-commands.js?v=6";\n'+s
  replacement='if((lt==null?void 0:lt.type)==="prompt"){const cmd=String(lt.content??"").startsWith("DCC_COMMAND:")?String(lt.content).slice(12):null;if(["plan","grill-me"].includes(cmd)){ft.chain().focus().deleteRange(Bt).run();dccSelectMode(cmd);return}Qr(ft,Bt,lt.content??"");return}'
  s=s.replace(anchor,replacement,1)
  anchor='if(String(w).trim()==="/status")';assert anchor in s
  s=s.replace(anchor,'if(["/plan","/grill-me"].includes(String(w).trim())){const mode=String(w).trim().slice(1);ao();dccSelectMode(mode);return}'+anchor,1)
  file.write_text(s)
 assert found
 # Deployment cache version only; native compact/new-chat handlers are untouched.
 for file in (root/'build/_app/immutable').rglob('*.js'):
  s=file.read_text();s=re.sub(r'(["\'])(\.{1,2}/[^"\'\s?]+\.js)\1',r'\1\2?dcccmd=6\1',s);file.write_text(s)
 index=root/'build/index.html';s=index.read_text();s=re.sub(r'(["\'])((?:\./|/)_app/immutable/entry/[^"\'\s?]+\.js)\1',r'\1\2?dcccmd=6\1',s);index.write_text(s)
if __name__=='__main__':patch(sys.argv[1])
