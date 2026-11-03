// Approved vocabulary rules in the interface: editing, approval, replacement annotations in both modes,
// processing stages and stale-result guards. Runs the real page script with mocked network and microphone.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const html=fs.readFileSync(require('node:path').join(__dirname,'..','src','arabic_relay','index.html'),'utf8');
const nodes=new Map();
function element(){return {value:'',disabled:false,hidden:false,textContent:'',innerHTML:'',style:{setProperty(){}},dataset:{},classList:{toggle(){},remove(){}},listeners:{},addEventListener(name,fn){this.listeners[name]=fn;},setAttribute(){},removeAttribute(){},querySelector(){return element();},append(){},after(){},remove(){},focus(){this.focused=true;},showModal(){this.open=true;},close(){this.open=false;this.listeners.close?.();},click(){return this.listeners.click?.();}};}
for(const match of html.matchAll(/id="([^"]+)"/g))nodes.set(match[1],element());
const requests=[],storage=new Map();
let rules=[{id:1,heard:'ريدزاين',canonical:'redesign',enabled:1,provenance:'user'},];
let proposals=[{heard:'لوجين فلو',spelling:'login flow'}];
let translateGate=null,releaseTranslate=null;
class Recorder{
 static isTypeSupported(t){return t==='audio/webm;codecs=opus';}
 constructor(){this.state='inactive';}
 start(){this.state='recording';}
 stop(){this.state='inactive';this.ondataavailable({data:new Blob([new Uint8Array([26,69,223,163,1])])});queueMicrotask(()=>this.onstop());}
}
const transcription={text:'عايز أعمل redesign ومش أغير login flow',rawText:'عايز أعمل ريدزاين ومش أغير اللوك فلو',
 replacements:[{heard:'ريدزاين',canonical:'redesign',replacement:'redesign'}],
 timings:{path:'incremental',stopToFinalMs:1230},incremental:{used:true,windows:3}};
const context=vm.createContext({console,crypto:require('node:crypto'),Blob,Map,Set,JSON,encodeURIComponent,queueMicrotask,setTimeout,clearTimeout,MediaRecorder:Recorder,
 document:{getElementById:id=>nodes.get(id)||null,createElement:element,documentElement:{dataset:{}},addEventListener(){}},
 localStorage:{getItem(key){return storage.get(key)||'';},setItem(key,value){storage.set(key,value);}},
 navigator:{mediaDevices:{async getUserMedia(){return {getTracks:()=>[{stop(){}}]};}}},
 URL:{createObjectURL(){return 'blob:mock';},revokeObjectURL(){}},
 fetch:async(url,options)=>{requests.push({url,options});let data,ok=true;
  if(url==='/api/status')data={csrf:'test-token',speechConfigured:true,speechModel:'Whisper large-v3',speechDevice:'GPU',setupNeeded:false,promptChoice:{tool:'codex',model:'',effort:'low'},promptLabel:'Codex · Default · low',selfLearning:true,autoSpelling:false,incrementalDictation:false};
  else if(url==='/api/vocabulary')data={words:['React'],corrections:[{heard:'لوجين فلو',spelling:'login flow'}]};
  else if(url==='/api/aliases')data={aliases:rules,proposals};
  else if(url==='/api/aliases/add'){const b=JSON.parse(options.body);rules=rules.concat([{id:rules.length+9,heard:b.heard,canonical:b.canonical,enabled:1,provenance:b.provenance||'user'}]);proposals=proposals.filter(p=>p.heard!==b.heard);data={aliases:rules,proposals};}
  else if(url==='/api/aliases/update'){const b=JSON.parse(options.body);rules=rules.map(r=>r.id===b.id?{...r,enabled:b.enabled?1:0}:r);data={aliases:rules,proposals};}
  else if(url==='/api/aliases/delete'){const b=JSON.parse(options.body);rules=rules.filter(r=>r.id!==b.id);data={aliases:rules,proposals};}
  else if(url==='/api/aliases/apply'){const b=JSON.parse(options.body);const text=b.text.replace(/ريدزاين/g,'redesign');data={text,original:b.text,replacements:text===b.text?[]:[{heard:'ريدزاين',canonical:'redesign',replacement:'redesign'}],conflicts:[]};}
  else if(url==='/api/transcribe')data=transcription;
  else if(url==='/api/translate'){if(translateGate)await translateGate;data={english:'Redesign the page.',replacements:transcription.replacements,originalSource:transcription.rawText,vocabulary:{words:[],corrections:[]}};}
  else if(url==='/api/stream/start')data={accepted:false,reason:'disabled'};
  else if(url==='/api/speech/prepare'||url==='/api/speech/cancel-prepare')data={prepared:true};
  else if(url==='/api/inline'){const b=JSON.parse(options.body);data={status:'ready',requestId:b.requestId,revision:b.revision,workingText:b.text,outputText:'I want to redesign the page',issues:[],replacements:[]};}
  else throw new Error('Unexpected request '+url);
  return {ok,json:async()=>data};
 }});
context.window={location:{search:''},MediaRecorder:Recorder,addEventListener(){}};
context.confirm=()=>true;
for(const script of html.matchAll(/<script>([\s\S]*?)<\/script>/g))vm.runInContext(script[1],context);
const flush=async(n=4)=>{for(let i=0;i<n;i++)await new Promise(resolve=>setTimeout(resolve,0));};
const count=url=>requests.filter(r=>r.url===url).length;
(async()=>{
 await flush();
 // 1. Rules and proposals are listed; each control calls the matching route.
 assert.equal(nodes.get('aliasStatus').textContent,'1 rule · 1 active · 1 proposal');
 assert.ok(nodes.get('aliasList').innerHTML.includes('ريدزاين'));
 assert.ok(nodes.get('aliasProposals').innerHTML.includes('login flow'));
 nodes.get('aliasHeard').value='دارك مود';nodes.get('aliasCanonical').value='dark mode';
 nodes.get('aliasList').listeners.click({target:{closest:()=>null}});
 await nodes.get('aliasAdd').click();await flush();
 const added=JSON.parse(requests.filter(r=>r.url==='/api/aliases/add').at(-1).options.body);
 assert.deepEqual(added,{heard:'دارك مود',canonical:'dark mode'});
 assert.equal(nodes.get('aliasHeard').value,'');
 assert.ok(nodes.get('aliasList').innerHTML.includes('dark mode'));
 await nodes.get('aliasProposals').listeners.click({target:{closest:()=>({dataset:{aliasApprove:'لوجين فلو',aliasCanonical:'login flow'}})}});
 await flush();
 assert.equal(JSON.parse(requests.filter(r=>r.url==='/api/aliases/add').at(-1).options.body).provenance,'learned');
 await nodes.get('aliasList').listeners.click({target:{closest:sel=>sel==='[data-alias-toggle]'?{dataset:{aliasToggle:'1'},checked:false}:null}});
 await flush();
 assert.deepEqual(JSON.parse(requests.filter(r=>r.url==='/api/aliases/update').at(-1).options.body),{id:1,enabled:false});
 await nodes.get('aliasList').listeners.click({target:{closest:sel=>sel==='[data-alias-delete]'?{dataset:{aliasDelete:'1'}}:null}});
 await flush();
 assert.equal(JSON.parse(requests.filter(r=>r.url==='/api/aliases/delete').at(-1).options.body).id,1);
 // 2. A transcript: rules already applied by the server are announced; the original stays available.
 rules=[{id:1,heard:'ريدزاين',canonical:'redesign',enabled:1,provenance:'user'}];
 await nodes.get('record').click();await nodes.get('record').click();await flush(8);
 assert.equal(nodes.get('arabic').value,'عايز أعمل redesign ومش أغير login flow');
 assert.equal(nodes.get('originalSpeechText').textContent,'عايز أعمل ريدزاين ومش أغير اللوك فلو');   // the recognition output is preserved
 assert.ok(nodes.get('notice').textContent.includes('Vocabulary: ريدزاين → redesign'),nodes.get('notice').textContent);
 assert.equal(nodes.get('stageStatus').hidden,false);
 assert.ok(nodes.get('stageStatus').textContent.includes('Transcript 1.2 s after Stop'),nodes.get('stageStatus').textContent);
 assert.ok(nodes.get('stageStatus').textContent.includes('3 windows while you spoke'));
 // 3. Prompt preparation keeps the original source and shows the replaced form.
 await nodes.get('clearDraft').click();
 nodes.get('arabic').value='اعمل ريدزاين';await nodes.get('translate').click();await flush(6);
 assert.equal(nodes.get('english').value,'Redesign the page.');
 assert.equal(nodes.get('arabic').value,'اعمل ريدزاين');
 assert.equal(nodes.get('stageStatus').textContent,'Prompt ready');
 // 4. Inline: approved rules rewrite the finalized source before translation.
 await nodes.get('inlineMode').click();await nodes.get('clearDraft').click();
 nodes.get('english').value='اعمل ريدزاين للصفحة';
 const beforeInline=count('/api/inline');
 await nodes.get('translate').click();await flush(8);
 assert.equal(count('/api/aliases/apply'),1);
 const inline=JSON.parse(requests.filter(r=>r.url==='/api/inline').at(-1).options.body);
 assert.equal(inline.text,'اعمل redesign للصفحة',inline.text);
 assert.equal(count('/api/inline'),beforeInline+1);
 assert.ok(nodes.get('inlineChanges').innerHTML.includes('redesign'));
 // 5. A late preparation result never replaces newer text.
 await nodes.get('craftMode').click();await nodes.get('clearDraft').click();
 nodes.get('arabic').value='اعمل ريدزاين';
 translateGate=new Promise(resolve=>{releaseTranslate=resolve;});
 const pending=nodes.get('translate').click();await flush();
 vm.runInContext('jobGeneration++',context);
 releaseTranslate();await pending;await flush(6);
 assert.equal(nodes.get('english').value,'');
 console.log('Vocabulary rules, proposals, both modes, stages and stale-result guards verified. No real uploads or model calls.');
})().catch(e=>{console.error(e);process.exitCode=1;});
