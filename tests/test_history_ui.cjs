// Exercise the real interface without a microphone, model call, or external delivery.
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'..','src','arabic_relay','index.html'),'utf8');
const nodes=new Map();
function element(){return {value:'',disabled:false,textContent:'',innerHTML:'',attributes:{},style:{setProperty(){}},dataset:{},classList:{toggle(){},remove(){}},listeners:{},addEventListener(n,f){this.listeners[n]=f;},setAttribute(n,v){this.attributes[n]=v;},getAttribute(n){return this.attributes[n]??null;},removeAttribute(){},querySelector(){return element();},append(){},after(){},remove(){},focus(){this.focused=true;},showModal(){this.open=true;},close(){this.open=false;this.listeners.close?.();},click(){return this.listeners.click?.();}};}
for(const m of html.matchAll(/id="([^"]+)"/g))nodes.set(m[1],element());
const requests=[],storage=new Map([['relay-language','ar']]);let copied='',historyFailure=false;
let entries=[{id:'saved',english:'Keep <script>alert("test")</script> as text.\nSecond line.',sentAt:1700000000,destinations:['Project / <session>']}];
class Recorder{static isTypeSupported(){return true;}start(){this.state='recording';}stop(){this.state='inactive';this.ondataavailable({data:new Blob(['test'])});queueMicrotask(()=>this.onstop());}}
const context=vm.createContext({console,crypto:require('node:crypto'),Blob,Map,Set,JSON,encodeURIComponent,queueMicrotask,setTimeout,clearTimeout,MediaRecorder:Recorder,
 confirm:()=>{throw Error('Native confirmation must not open');},
 document:{getElementById:id=>nodes.get(id)||null,createElement:element,documentElement:{dataset:{}},addEventListener(){}},
 localStorage:{getItem:k=>storage.get(k)||'',setItem:(k,v)=>storage.set(k,v)},
 navigator:{clipboard:{writeText:async text=>{copied=text;}},mediaDevices:{getUserMedia:async()=>({getTracks:()=>[{stop(){}}]})}},
 URL:{createObjectURL:()=> 'blob:test',revokeObjectURL(){}},
 fetch:async(url,options)=>{requests.push({url,options});let data;
  if(url==='/api/status')data={csrf:'test',connected:false,speechConfigured:true,speechModel:'Whisper large-v3',setupNeeded:false,promptChoice:{tool:'codex',model:'',effort:'low'}};
  else if(url==='/api/catalog')data={projects:[],live:false};
  else if(url==='/api/vocabulary')data={words:[],corrections:[]};
  else if(url==='/api/history')data=historyFailure?{error:'History unavailable'}:{entries};
  else if(url==='/api/history/delete'){entries=entries.filter(e=>e.id!==JSON.parse(options.body).id);data={entries};}
  else if(url==='/api/history/clear'){entries=[];data={entries};}
  else if(url==='/api/transcribe')data={text:'New transcript',rawText:'New transcript'};
  else if(url==='/api/translators')data={choice:{tool:'codex',model:'',effort:'low'},label:'Codex',efforts:['low'],codex:{installed:true,models:[]},claude:{installed:false,models:[]}};
  else if(url==='/api/setup')data={models:{},recommended:'large-v3'};
  else if(url==='/api/setup/progress')data={state:'idle'};
  else if(url.startsWith('/api/local-translator'))data={state:'idle',enabled:false,installed:false,running:false,model:'gemma4:e4b'};
  else throw Error('Unexpected request '+url);
  return {ok:!(url==='/api/history'&&historyFailure),json:async()=>data};
 }});
context.window={MediaRecorder:Recorder,addEventListener(){}};
for(const script of html.matchAll(/<script>([\s\S]*?)<\/script>/g))vm.runInContext(script[1],context);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const run=code=>vm.runInContext(code,context);
(async()=>{
 await flush();await nodes.get('historyButton').click();await flush();
 assert.equal(nodes.get('historyPanel').hidden,false);
 assert.equal(nodes.get('historySummary').textContent,'1 من 10');
 assert.ok(!html.includes('id="historyDialog"'));
 assert.ok(nodes.get('historyList').innerHTML.includes('&lt;script&gt;'));
 assert.ok(!nodes.get('historyList').innerHTML.includes('<script>'));
 assert.ok(nodes.get('historyList').innerHTML.includes('&lt;session&gt;'));
 await run("historyAction('copy','saved')");assert.equal(copied,entries[0].english);
 assert.equal(nodes.get('notice').textContent,'اتنسخ.');
 // Text that could not be pasted says so.
 entries=[{id:'kept',english:'Kept text',sentAt:1700000000,destinations:['اتنسخ، ما اتلصقش']}];
 await run('loadHistory()');assert.ok(nodes.get('historyList').innerHTML.includes('اتنسخ، ما اتلصقش'));
 entries=[{id:'saved',english:'Saved text',sentAt:1700000000,destinations:['Notepad']}];
 await run('loadHistory()');
 // There is nothing to send or reuse from here.
 await run("historyAction('reuse','saved')");
 assert.equal(nodes.get('english').value,'');
 assert.ok(!requests.some(r=>r.url==='/api/send'));
 // Delete and clear ask first, and a no changes nothing.
 const before=requests.length;
 let pending=run("historyAction('delete','saved')");
 assert.equal(nodes.get('confirmDialog').open,true);
 nodes.get('confirmCancel').click();await pending;
 pending=nodes.get('clearHistory').click();
 nodes.get('confirmCancel').click();await pending;
 assert.equal(requests.length,before);assert.equal(entries.length,1);
 pending=run("historyAction('delete','saved')");nodes.get('confirmAccept').click();await pending;await flush();
 assert.equal(entries.length,0);assert.equal(nodes.get('clearHistory').disabled,true);
 assert.ok(nodes.get('historyList').innerHTML.includes('لسه مفيش حاجة هنا'));
 entries=[{id:'another',english:'Another prompt',sentAt:1700000000,destinations:['Session']}];
 await run('loadHistory()');pending=nodes.get('clearHistory').click();nodes.get('confirmAccept').click();await pending;await flush();
 assert.equal(entries.length,0);
 // A paste from the pill refreshes the list when History is the open tab, and only then.
 entries=[{id:'fresh',english:'Fresh paste',sentAt:1700000000,destinations:['Notepad']}];
 context.window.relayHistoryChanged();await flush();
 assert.ok(nodes.get('historyList').innerHTML.includes('Fresh paste'));
 await nodes.get('settingsButton').click();const asked=requests.length;
 context.window.relayHistoryChanged();await flush();
 assert.equal(requests.length,asked);
 await nodes.get('historyButton').click();await flush();
 historyFailure=true;await run('loadHistory()');assert.equal(nodes.get('historyMessage').textContent,'History unavailable');
 historyFailure=false;
 nodes.get('arabic').value='Keep my transcript';nodes.get('english').value='Keep my prompt';
 await nodes.get('record').click();assert.equal(nodes.get('recordActions').hidden,false);
 await nodes.get('cancelRecording').click();await flush();
 assert.equal(nodes.get('recordActions').hidden,true);assert.equal(nodes.get('arabic').value,'Keep my transcript');assert.equal(nodes.get('english').value,'Keep my prompt');
 assert.equal(requests.filter(r=>r.url==='/api/transcribe').length,0);
 await nodes.get('record').click();await nodes.get('stopRecording').click();await flush();await flush();
 assert.equal(requests.filter(r=>r.url==='/api/transcribe').length,1);
 assert.ok(nodes.get('arabic').value.includes('New transcript'));
 assert.ok(!requests.some(r=>r.url==='/api/send'));
 console.log('History and recording interface checks passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
