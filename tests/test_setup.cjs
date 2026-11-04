// Runs the real interface script against scripted setup replies: first launch and the Models tab's speech section.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const html=fs.readFileSync(require('node:path').join(__dirname,'..','src','arabic_relay','index.html'),'utf8');
const GPU={name:'NVIDIA GeForce RTX 4080 Laptop GPU',vramGb:12,driver:'616.92'};
const flush=async(times=8)=>{for(let i=0;i<times;i++)await new Promise(resolve=>setTimeout(resolve,2));};  // lets SETUP_POLL=0 timers run
const ABSENT={installed:false,active:false,path:null};

function boot({status={},setup={},progress=[{state:'idle'}],pollMs=0}={}){
 const nodes=new Map(),requests=[],storage=new Map([['relay-language','ar']]);
 function element(){return {value:'',disabled:false,hidden:false,checked:false,textContent:'',innerHTML:'',attributes:{},style:{setProperty(){}},dataset:{},classList:{toggle(){},remove(){},add(){}},listeners:{},addEventListener(name,fn){this.listeners[name]=fn;},dispatchEvent(event){return this.listeners[event.type]?.(event);},setAttribute(name,value){this.attributes[name]=value;},getAttribute(name){return this.attributes[name]??null;},removeAttribute(){},querySelector(){return element();},append(){},after(){},remove(){},focus(){},showModal(){this.open=true;},close(){this.open=false;this.listeners.close?.();},click(){return this.listeners.click?.();}};}
 for(const match of html.matchAll(/id="([^"]+)"/g))nodes.set(match[1],element());
 for(const match of html.matchAll(/<[^>]*\sid="([^"]+)"[^>]*>/g))if(/\shidden[\s>]/.test(match[0]))nodes.get(match[1]).hidden=true;
 const replies=[{state:'idle'},...progress];  // opening the Models tab asks once before anything starts
 const context=vm.createContext({console,Blob,Map,Set,JSON,Event:class{constructor(type){this.type=type;}},encodeURIComponent,decodeURIComponent,queueMicrotask,setTimeout,clearTimeout,
  MediaRecorder:class{static isTypeSupported(){return true;}},
  document:{getElementById:id=>nodes.get(id)||null,createElement:element,documentElement:{dataset:{}},addEventListener(){}},
  localStorage:{getItem(key){return storage.get(key)||'';},setItem(key,value){storage.set(key,value);}},
  navigator:{mediaDevices:{}},confirm:()=>{throw Error('Native confirmation must not open');},
  fetch:async(url,options)=>{requests.push({url,body:options?.body?JSON.parse(options.body):undefined});let data,ok=true;
   if(url==='/api/status')data={csrf:'t',connected:false,selfLearning:true,autoSpelling:false,speechConfigured:false,setupNeeded:true,...status};
   else if(url==='/api/catalog')data={projects:[],live:false};
   else if(url==='/api/vocabulary')data={words:[],corrections:[]};
   else if(url==='/api/translators')data={choice:{tool:'codex',model:'',effort:'low'},label:'Codex',efforts:['low'],codex:{installed:true,models:[]},claude:{installed:false,models:[]}};
   else if(url==='/api/setup')data={gpu:null,gpuRuntimeReady:false,codex:true,claude:false,wisprFlow:true,freeGb:120.4,recommended:'large-v3-turbo',customModel:null,
     models:{'large-v3':{gb:3.1,...ABSENT},'large-v3-turbo':{gb:1.6,...ABSENT}},gpuDownloadGb:0,...setup};
   else if(url==='/api/setup/download'||url==='/api/setup/progress'){data=replies.length>1?replies.shift():replies[0];if(data?.error&&data.state!=='error'){ok=false;}}
   else if(url==='/api/setup/cancel'){data={state:'cancelled',stage:'model',model:'large-v3-turbo',done:1,total:2};replies.splice(0,replies.length,data);}
   else if(url==='/api/setup/skip')data={setupNeeded:false};
   else if(url==='/api/speech-device')data={cpuOnly:JSON.parse(options.body).cpuOnly};
   else if(url==='/api/model/delete')data={speechConfigured:false};
   else if(url==='/api/whisper-model')data={speechConfigured:true,modelPath:'C:\\Models\\large-v3'};
   else if(url==='/api/local-translator')data={enabled:true,installed:false,running:false,model:'gemma4:e4b'};
   else if(url==='/api/local-translator/progress')data={state:'idle'};
   else data={};
   return {ok,json:async()=>data};}});
 context.window={MediaRecorder:context.MediaRecorder,addEventListener(){},pywebview:{api:{pick_folder:async()=> 'C:\\Models\\large-v3'}}};
 for(const script of html.matchAll(/<script>([\s\S]*?)<\/script>/g))vm.runInContext(script[1],context);
 vm.runInContext('SETUP_POLL='+pollMs,context);
 const press=(action,name)=>nodes.get('speechModels').listeners.click({target:{closest:selector=>selector==='[data-model-action]'?{disabled:false,dataset:{modelAction:action,modelName:name}}:null}});
 return {nodes,requests,storage,context,press,posted:url=>requests.filter(r=>r.url===url),rows:()=>nodes.get('speechModels').innerHTML};
}
const rowOf=(rows,name)=>rows.split('<div class="row">').find(row=>row.includes(`Whisper ${name}<`))||'';

(async()=>{
 // `.btn{display:inline-flex}` must not beat the hidden attribute.
 assert.ok(html.includes('[hidden]{display:none!important}'),'global [hidden] rule missing');

 // First launch opens on Models. A GPU computer is offered large-v3, with the NVIDIA library's size.
 let app=boot({setup:{gpu:GPU,recommended:'large-v3',gpuDownloadGb:0.55}});
 await flush();
 let n=app.nodes;
 assert.equal(n.get('modelsPanel').hidden,false);
 assert.equal(n.get('historyPanel').hidden,true);
 assert.equal(n.get('modelsButton').attributes['aria-selected'],'true');
 let big=rowOf(app.rows(),'large-v3'),small=rowOf(app.rows(),'large-v3-turbo');
 assert.ok(big.includes('مقترح لجهازك')&&big.includes('btn primary'));
 assert.ok(big.includes('0.55 GB')&&big.includes('مكتبة NVIDIA'));
 assert.ok(!small.includes('مقترح لجهازك')&&!small.includes('btn primary'));
 assert.equal(n.get('dictationHelp').textContent,'عشان تبدأ، حمّل نموذج كلام أو اختار واحد عندك.');
 assert.equal(n.get('pickModel').hidden,true,'the browser cannot pick a folder');

 // No GPU: turbo is the one offered.
 app=boot();
 await flush();
 assert.ok(rowOf(app.rows(),'large-v3-turbo').includes('مقترح لجهازك'));
 assert.ok(!rowOf(app.rows(),'large-v3').includes('مقترح لجهازك'));

 // Download: the row shows progress and offers cancel, and done says so and shows the model in use.
 app=boot({setup:{gpu:GPU,recommended:'large-v3'},pollMs:60000,progress:[
  {state:'downloading',stage:'model',model:'large-v3',done:0,total:3.09e9},
  {state:'downloading',stage:'model',model:'large-v3',done:1.5e9,total:3.09e9},
  {state:'done',stage:'check',model:'large-v3',done:3.09e9,total:3.09e9,device:'GPU',notice:null}]});
 await flush();
 n=app.nodes;
 await app.press('download','large-v3');
 assert.deepEqual(app.posted('/api/setup/download')[0].body,{model:'large-v3',fresh:false});
 assert.ok(rowOf(app.rows(),'large-v3').includes('data-model-action="cancel"'));
 assert.ok(rowOf(app.rows(),'large-v3-turbo').includes('disabled'),'one download at a time');
 await app.context.pollSetup();
 assert.ok(rowOf(app.rows(),'large-v3').includes('1.5 GB'),app.rows());
 // The speed reads as its own run; right-to-left text must not pull the separator next to the size.
 vm.runInContext("setupSpeed='21 MB/s';renderSpeechModels();",app.context);
 assert.ok(rowOf(app.rows(),'large-v3').includes(' · <bdi dir="ltr">21 MB/s</bdi></p>'),app.rows());
 await app.context.pollSetup();await flush();
 assert.equal(n.get('notice').textContent,'الكلام جاهز.');

 // The job's notice (GPU fell back to CPU) is shown with it.
 app=boot({pollMs:60000,progress:[{state:'downloading',stage:'model',model:'large-v3-turbo',done:0,total:1},{state:'done',stage:'check',device:'CPU',notice:'كارت الشاشة ما قدرش يشغّل النموذج.'}]});
 await flush();
 await app.press('download','large-v3-turbo');await app.context.pollSetup();await flush();
 assert.equal(app.nodes.get('notice').textContent,'الكلام جاهز. كارت الشاشة ما قدرش يشغّل النموذج.');

 // A failed download says why and can be started again.
 app=boot({pollMs:60000,progress:[{state:'downloading',stage:'model',model:'large-v3-turbo',done:0,total:1},{state:'error',stage:'model',model:'large-v3-turbo',done:0,total:1,error:'التحميل وقف. راجع النت وجرّب تاني، وهيكمل من مكانه.'}]});
 await flush();
 await app.press('download','large-v3-turbo');await app.context.pollSetup();await flush();
 assert.ok(app.nodes.get('notice').textContent.startsWith('التحميل وقف.'));
 assert.ok(rowOf(app.rows(),'large-v3-turbo').includes('data-model-action="download"'));
 assert.ok(!rowOf(app.rows(),'large-v3-turbo').includes(' disabled'));

 // Cancel pauses; the row offers to carry on from there.
 app=boot({pollMs:60000,progress:[{state:'downloading',stage:'model',model:'large-v3-turbo',done:1,total:2}]});
 await flush();
 await app.press('download','large-v3-turbo');
 await app.press('cancel');await flush();
 assert.equal(app.posted('/api/setup/cancel').length,1);
 assert.ok(rowOf(app.rows(),'large-v3-turbo').includes('كمّل التحميل'));
 assert.ok(rowOf(app.rows(),'large-v3-turbo').includes('وقف.'));

 // Choosing Wispr Flow instead of downloading ends first-run setup.
 app=boot();
 await flush();
 n=app.nodes;
 await n.get('useWispr').click();await flush();
 assert.equal(app.posted('/api/setup/skip').length,1);
 assert.equal(n.get('dictationProvider').value,'wispr');
 assert.equal(app.storage.get('relay-dictation-provider'),'wispr');
 assert.equal(n.get('wisprBadge').hidden,false);

 // Desktop window: a model folder the user already has can be picked.
 app=boot({status:{nativeDesktop:true}});
 await flush();
 n=app.nodes;
 assert.equal(n.get('pickModel').hidden,false);
 await n.get('pickModel').click();await flush();
 assert.deepEqual(app.posted('/api/whisper-model')[0].body,{path:'C:\\Models\\large-v3'});
 assert.equal(n.get('notice').textContent,'اتحفظ فولدر النموذج.');

 // Already set up: opens on History, and asks about models only when the tab opens.
 const ready={setupNeeded:false,speechConfigured:true,speechModel:'Whisper large-v3',speechDevice:'GPU',modelOwned:true,cpuOnly:false,missingModel:null};
 const installed={installed:true,active:true,path:'C:\\Data\\models\\large-v3'};
 app=boot({status:ready,setup:{gpu:GPU,models:{'large-v3':{gb:3.1,...installed},'large-v3-turbo':{gb:1.6,installed:true,active:false,path:'C:\\Data\\models\\large-v3-turbo'}}}});
 await flush();
 n=app.nodes;
 assert.equal(n.get('historyPanel').hidden,false);
 assert.equal(n.get('modelsPanel').hidden,true);
 assert.equal(app.posted('/api/setup').length,0);
 await n.get('modelsButton').click();await flush();
 assert.equal(n.get('modelsPanel').hidden,false);
 assert.equal(app.posted('/api/setup').length,1);
 big=rowOf(app.rows(),'large-v3');small=rowOf(app.rows(),'large-v3-turbo');
 assert.ok(big.includes('المستخدم حاليًا')&&big.includes('بيشتغل على كارت الشاشة'));
 assert.ok(small.includes('data-model-action="use"')&&small.includes('data-model-action="delete"'));
 assert.ok(!big.includes('data-model-action="download"'));

 // Using another installed model saves it; a delete asks first, by name, and a no sends nothing.
 await app.press('use','large-v3-turbo');await flush();
 assert.deepEqual(app.posted('/api/whisper-model').at(-1).body,{path:'C:\\Data\\models\\large-v3-turbo'});
 assert.equal(n.get('notice').textContent,'هنستخدم Whisper large-v3-turbo.');
 let pending=app.press('delete','large-v3-turbo');
 assert.equal(n.get('confirmDialog').open,true);
 assert.ok(n.get('confirmDescription').textContent.includes('large-v3-turbo'));
 n.get('confirmCancel').click();await pending;
 assert.equal(app.posted('/api/model/delete').length,0);
 pending=app.press('delete','large-v3-turbo');
 n.get('confirmAccept').click();await pending;await flush();
 assert.deepEqual(app.posted('/api/model/delete')[0].body,{name:'large-v3-turbo'});
 assert.equal(n.get('notice').textContent,'اتمسح النموذج.');

 // A model the user pointed at outside the app gets its own row and is never offered for deleting.
 app=boot({status:ready,setup:{customModel:{label:'my-whisper',path:'C:\\Models\\my-whisper'}}});
 await flush();
 await app.nodes.get('modelsButton').click();await flush();
 assert.ok(app.rows().includes('my-whisper')&&app.rows().includes('C:\\Models\\my-whisper'));
 const custom=app.rows().split('<div class="row">').find(row=>row.includes('my-whisper'));
 assert.ok(custom.includes('المستخدم حاليًا')&&!custom.includes('data-model-action="delete"'));

 // A model folder that moved is explained where the fix is.
 app=boot({status:{setupNeeded:false,speechConfigured:false,missingModel:'C:\\Models\\large-v3'}});
 await flush();
 assert.equal(app.nodes.get('dictationHelp').textContent,'فولدر النموذج اتنقل أو اتمسح: C:\\Models\\large-v3. حمّل نموذج أو اختار فولدر تاني.');

 // Always use CPU saves and refreshes.
 app=boot({status:ready});
 await flush();
 n=app.nodes;
 const statusCalls=app.posted('/api/status').length;
 n.get('cpuOnly').checked=true;await n.get('cpuOnly').listeners.change();await flush();
 assert.deepEqual(app.posted('/api/speech-device')[0].body,{cpuOnly:true});
 assert.ok(app.posted('/api/status').length>statusCalls);

 console.log('Models tab: first launch, recommended model, download progress, failure, pause, Wispr choice, folder pick, use and delete verified. No real downloads.');
 process.exit(0);  // stop any leftover poll timer
})().catch(error=>{console.error(error);process.exit(1);});
