const labels={normal:'通常',plan:'計画モード','grill-me':'質問モード'};
const modes=new Map();
let lastPath='',pending='normal';
const chatId=()=>location.pathname.match(/^\/c\/([^/]+)$/)?.[1];
const tokenHeaders=()=>({'Content-Type':'application/json',Authorization:`Bearer ${localStorage.getItem('token')||''}`});
export function getDccMode(){return modes.get(chatId()||'new')||'normal';}
function render(){
 const input=document.getElementById('chat-input'); if(!input)return;
 let bar=document.getElementById('dcc-mode-bar');
 if(!bar){bar=document.createElement('div');bar.id='dcc-mode-bar';bar.setAttribute('role','status');bar.style.cssText='display:flex;gap:12px;align-items:center;padding:6px 12px;font-size:13px';input.parentElement.before(bar);}
 const mode=getDccMode();if(bar.dataset.mode===mode)return;bar.dataset.mode=mode;bar.replaceChildren();bar.hidden=mode==='normal';
 if(mode!=='normal'){const label=document.createElement('span');label.textContent=labels[mode]+' · 実行は行いません';const reset=document.createElement('button');reset.type='button';reset.textContent='通常に戻す';reset.style.textDecoration='underline';reset.onclick=()=>selectDccMode('normal');bar.append(label,reset);}
}
export async function selectDccMode(mode){
 if(!(mode in labels))return;
 const cid=chatId();
 if(cid){const r=await fetch(`/api/v1/dcc/commands/mode`,{method:'POST',headers:tokenHeaders(),body:JSON.stringify({chat_id:cid,mode})});if(!r.ok){const d=await r.json();alert(typeof d.detail==='string'?d.detail:'モードを切り替えられませんでした');return;}}
 modes.set(cid||'new',mode);pending=mode;sessionStorage.setItem('dcc-mode:'+ (cid||'new'),mode);render();
}
// Add only structured mode metadata to the normal chat request; never modify its user text.
const originalFetch=window.fetch.bind(window);
window.fetch=function(input,init){
 const url=typeof input==='string'?input:input instanceof Request?input.url:String(input);
 if(new URL(url,location.origin).pathname==='/api/chat/completions'&&init?.method?.toUpperCase()==='POST'&&typeof init.body==='string'){
  try{const body=JSON.parse(init.body);body.dcc_mode=getDccMode();if(!chatId())sessionStorage.removeItem('dcc-mode:new');init={...init,body:JSON.stringify(body)};}catch{}
 }
 return originalFetch(input,init);
};
async function sync(){
 if(lastPath!==location.pathname){lastPath=location.pathname;const cid=chatId();
  if(cid){modes.set(cid,sessionStorage.getItem('dcc-mode:'+cid)||pending);pending='normal';const savedPath=lastPath;
   try{const r=await originalFetch('/api/v1/dcc/commands/mode?chat_id='+encodeURIComponent(cid),{headers:tokenHeaders()});if(r.ok&&location.pathname===savedPath){const d=await r.json();modes.set(cid,d.mode);render();}}catch{}
  }else modes.set('new',sessionStorage.getItem('dcc-mode:new')||'normal');
 }
 render();
}
let timer;
function schedule(){clearTimeout(timer);timer=setTimeout(sync,50);}
if(document.body)new MutationObserver(schedule).observe(document.body,{childList:true,subtree:true});
schedule();

let checkingMode=false;
setInterval(async()=>{
 if(document.hidden||checkingMode||!chatId()||getDccMode()!=='plan')return;
 checkingMode=true;const cid=chatId();
 try{const r=await originalFetch('/api/v1/dcc/commands/mode?chat_id='+encodeURIComponent(cid),{headers:tokenHeaders()});if(r.ok&&chatId()===cid){const d=await r.json();modes.set(cid,d.mode);sessionStorage.setItem('dcc-mode:'+cid,d.mode);render();}}catch{}finally{checkingMode=false;}
},1500);
