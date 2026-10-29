// Exercise the real interface without a microphone, model call, or external delivery.
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'..','src','arabic_relay','index.html'),'utf8');
const nodes=new Map();
function element(){return {value:'',disabled:false,textContent:'',innerHTML:'',style:{setProperty(){}},dataset:{},classList:{toggle(){},remove(){}},listeners:{},addEventListener(n,f){this.listeners[n]=f;},setAttribute(){},removeAttribute(){},querySelector(){return element();},append(){},after(){},remove(){},focus(){this.focused=true;},showModal(){this.open=true;},close(){this.open=false;this.listeners.close?.();},click(){return this.listeners.click?.();}};}
for(const m of html.matchAll(/id="([^"]+)"/g))nodes.set(m[1],element());
const requests=[],confirmations=[],storage=new Map();let allow=false,copied='',historyFailure=false;
let entries=[{id:'saved',english:'Keep <script>alert("test")</script> as text.\nSecond line.',sentAt:1700000000,destinations:['Project / <session>']}];
class Recorder{static isTypeSupported(){return true;}start(){this.state='recording';}stop(){this.state='inactive';this.ondataavailable({data:new Blob(['test'])});queueMicrotask(()=>this.onstop());}}
const context=vm.createContext({console,crypto:require('node:crypto'),Blob,Map,Set,JSON,encodeURIComponent,queueMicrotask,setTimeout,clearTimeout,MediaRecorder:Recorder,
 confirm:message=>{confirmations.push(message);return allow;},
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
  else throw Error('Unexpected request '+url);
  return {ok:!(url==='/api/history'&&historyFailure),json:async()=>data};
 }});
context.window={MediaRecorder:Recorder,addEventListener(){}};
for(const script of html.matchAll(/<script>([\s\S]*?)<\/script>/g))vm.runInContext(script[1],context);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const run=code=>vm.runInContext(code,context);
(async()=>{
 await flush();await nodes.get('historyButton').click();await flush();
 assert.equal(nodes.get('historyDialog').open,true);
 assert.ok(nodes.get('historyList').innerHTML.includes('&lt;script&gt;'));
 assert.ok(!nodes.get('historyList').innerHTML.includes('<script>'));
 assert.ok(nodes.get('historyList').innerHTML.includes('&lt;session&gt;'));
 await run("historyAction('copy','saved')");assert.equal(copied,entries[0].english);
 nodes.get('arabic').value='Existing Arabic';nodes.get('english').value='Existing English';nodes.get('vocabulary').value='React';
 run("selected.set('session',{id:'session',projectId:'p',project:'P',title:'Session'});audioBlob=new Blob(['old']);learningOriginal='original';recognitionBase='base';update();");
 const choice=run('JSON.stringify(promptChoice)');
 await run("historyAction('reuse','saved')");
 assert.equal(nodes.get('english').value,'Existing English');assert.equal(run('selected.size'),1);assert.equal(run('audioBlob.size'),3);
 allow=true;await run("historyAction('reuse','saved')");
 assert.equal(nodes.get('english').value,entries[0].english);assert.equal(nodes.get('arabic').value,'');
 assert.equal(run('audioBlob'),null);assert.equal(run('selected.size'),0);assert.equal(run('learningOriginal+recognitionBase'),'');
 assert.equal(run('JSON.stringify(promptChoice)'),choice);assert.equal(nodes.get('vocabulary').value,'React');
 assert.equal(nodes.get('historyDialog').open,false);assert.equal(nodes.get('send').disabled,true);
 assert.ok(!requests.some(r=>r.url==='/api/send'));
 await nodes.get('historyButton').click();await flush();
 allow=false;const before=requests.length;await run("historyAction('delete','saved')");await nodes.get('clearHistory').click();
 assert.equal(requests.length,before);assert.equal(entries.length,1);
 allow=true;await run("historyAction('delete','saved')");assert.equal(entries.length,0);assert.equal(nodes.get('clearHistory').disabled,true);
 entries=[{id:'another',english:'Another prompt',sentAt:1700000000,destinations:['Session']}];
 await run('loadHistory()');await nodes.get('clearHistory').click();assert.equal(entries.length,0);
 assert.ok(confirmations.some(q=>q.startsWith('Delete')));assert.ok(confirmations.some(q=>q.startsWith('Clear all')));
 assert.ok(nodes.get('historyList').innerHTML.includes('No sent prompts yet'));
 historyFailure=true;await run('loadHistory()');assert.equal(nodes.get('historyMessage').textContent,'History unavailable');
 await nodes.get('historyClose').click();
 nodes.get('arabic').value='Keep my transcript';nodes.get('english').value='Keep my prompt';
 await nodes.get('record').click();assert.equal(nodes.get('recordActions').hidden,false);
 assert.equal(nodes.get('historyButton').disabled,true);
 await nodes.get('cancelRecording').click();await flush();
 assert.equal(nodes.get('recordActions').hidden,true);assert.equal(nodes.get('arabic').value,'Keep my transcript');assert.equal(nodes.get('english').value,'Keep my prompt');
 assert.equal(requests.filter(r=>r.url==='/api/transcribe').length,0);
 await nodes.get('record').click();await nodes.get('stopRecording').click();await flush();await flush();
 assert.equal(requests.filter(r=>r.url==='/api/transcribe').length,1);
 assert.ok(nodes.get('arabic').value.includes('New transcript'));
 assert.ok(!requests.some(r=>r.url==='/api/send'));
 console.log('History and recording interface checks passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
