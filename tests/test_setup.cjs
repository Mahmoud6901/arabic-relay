// Runs the real interface script against scripted setup replies: first-launch dialog and Transcription settings.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const html=fs.readFileSync(require('node:path').join(__dirname,'..','src','arabic_relay','index.html'),'utf8');
const GPU={name:'NVIDIA GeForce RTX 4080 Laptop GPU',vramGb:12,driver:'616.92'};
const NO_GPU_COPY='No NVIDIA GPU, so transcription will run on the CPU and take a few seconds per sentence.';
const flush=async(times=8)=>{for(let i=0;i<times;i++)await new Promise(resolve=>setTimeout(resolve,2));};  // lets SETUP_POLL=0 timers run

function boot({status={},setup={},progress=[],confirmReply=true,transcribe=null}={}){
 const nodes=new Map(),requests=[],storage=new Map();
 function element(){return {value:'',disabled:false,hidden:false,checked:false,textContent:'',innerHTML:'',style:{setProperty(){}},dataset:{},classList:{toggle(){},remove(){},add(){}},listeners:{},addEventListener(name,fn){this.listeners[name]=fn;},dispatchEvent(event){return this.listeners[event.type]?.(event);},setAttribute(){},removeAttribute(){},querySelector(){return element();},append(){},after(){},remove(){},focus(){},showModal(){this.open=true;},close(){this.open=false;this.listeners.close?.();},click(){return this.listeners.click?.();}};}
 for(const match of html.matchAll(/id="([^"]+)"/g))nodes.set(match[1],element());
 for(const match of html.matchAll(/<[^>]*\sid="([^"]+)"[^>]*>/g))if(/\shidden[\s>]/.test(match[0]))nodes.get(match[1]).hidden=true;
 const replies=[...progress];
 const context=vm.createContext({console,Blob,Map,Set,JSON,Event:class{constructor(type){this.type=type;}},encodeURIComponent,decodeURIComponent,queueMicrotask,setTimeout,clearTimeout,
  MediaRecorder:class{static isTypeSupported(){return true;}},
  document:{getElementById:id=>nodes.get(id)||null,createElement:element,documentElement:{dataset:{}},addEventListener(){}},
  localStorage:{getItem(key){return storage.get(key)||'';},setItem(key,value){storage.set(key,value);}},
  navigator:{mediaDevices:{}},confirm:()=>confirmReply,
  fetch:async(url,options)=>{requests.push({url,body:options?.body?JSON.parse(options.body):undefined});let data,ok=true;
   if(url==='/api/status')data={csrf:'t',connected:false,selfLearning:true,autoSpelling:false,speechConfigured:false,setupNeeded:true,...status};
   else if(url==='/api/catalog')data={projects:[],live:false};
   else if(url==='/api/vocabulary')data={words:[],corrections:[]};
   else if(url==='/api/setup')data={gpu:null,gpuRuntimeReady:false,codex:true,claude:false,wisprFlow:true,freeGb:120.4,recommended:'large-v3-turbo',
     models:{'large-v3':{gb:3.1},'large-v3-turbo':{gb:1.6}},gpuDownloadGb:0,...setup};
   else if(url==='/api/setup/download'||url==='/api/setup/progress'){data=replies.length>1?replies.shift():replies[0];if(data?.error&&data.state!=='error'){ok=false;}}
   else if(url==='/api/setup/cancel'){data={state:'cancelled',stage:'model',done:1,total:2};replies.splice(0,replies.length,data);}
   else if(url==='/api/setup/skip')data={setupNeeded:false};
   else if(url==='/api/speech-device')data={cpuOnly:JSON.parse(options.body).cpuOnly};
   else if(url==='/api/model/delete')data={speechConfigured:false};
   else if(url==='/api/whisper-model')data={speechConfigured:true,modelPath:'C:\\Models\\large-v3'};
   else if(url==='/api/transcribe')data=transcribe;
   else data={};
   return {ok,json:async()=>data};}});
 context.window={MediaRecorder:context.MediaRecorder,addEventListener(){},pywebview:{api:{pick_folder:async()=> 'C:\\Models\\large-v3'}}};
 for(const script of html.matchAll(/<script>([\s\S]*?)<\/script>/g))vm.runInContext(script[1],context);
 vm.runInContext('SETUP_POLL=0',context);
 return {nodes,requests,storage,context,posted:url=>requests.filter(r=>r.url===url)};
}

(async()=>{
 // `.btn{display:inline-flex}` must not beat the hidden attribute (Cancel, Delete model, Use a model… rely on it).
 assert.ok(html.includes('[hidden]{display:none!important}'),'global [hidden] rule missing');
 // GPU computer: large-v3 preselected, no CPU warning.
 let app=boot({setup:{gpu:GPU,recommended:'large-v3',gpuDownloadGb:0.55}});
 await flush();
 let n=app.nodes;
 assert.equal(n.get('setupDialog').open,true);
 assert.equal(n.get('setupGpu').textContent,'NVIDIA GeForce RTX 4080 Laptop GPU · 12 GB');
 assert.equal(n.get('setupModel').value,'large-v3');
 assert.equal(n.get('setupCpuWarning').hidden,true);
 assert.ok(n.get('setupDownloadSize').textContent.includes('0.55 GB NVIDIA library'));
 assert.equal(n.get('setupPick').hidden,true);

 // No GPU: turbo preselected, exact warning.
 app=boot();
 await flush();
 n=app.nodes;
 assert.equal(n.get('setupModel').value,'large-v3-turbo');
 assert.equal(n.get('setupCpuWarning').hidden,false);
 assert.equal(html.match(/id="setupCpuWarning"[^>]*>([^<]*)</)[1],NO_GPU_COPY);  // static copy; the fake DOM has no markup text
 assert.equal(n.get('setupGpu').textContent,'No NVIDIA GPU');

 // Download: progress text, then done closes the dialog and announces it.
 app=boot({setup:{gpu:GPU,recommended:'large-v3'},progress:[
  {state:'downloading',stage:'model',done:0,total:3.09e9},
  {state:'downloading',stage:'model',done:1.5e9,total:3.09e9},
  {state:'done',stage:'check',done:3.09e9,total:3.09e9,device:'GPU',notice:null}]});
 await flush();
 n=app.nodes;
 await n.get('setupStart').click();
 assert.deepEqual(app.posted('/api/setup/download')[0].body,{model:'large-v3',fresh:false});
 await flush(3);
 assert.ok(n.get('setupProgressText').textContent.startsWith('Downloading the speech model · 1.5 of 3.1 GB'),n.get('setupProgressText').textContent);
 await flush(20);
 assert.equal(n.get('setupDialog').open,false);
 assert.equal(n.get('notice').textContent,'Speech is ready.');

 // The job's notice (GPU fell back to CPU) is shown with it.
 app=boot({progress:[{state:'done',stage:'check',device:'CPU',notice:"The GPU couldn't run the speech model, so speech will use the CPU. You can turn this off in Settings."}]});
 await flush();
 await app.nodes.get('setupStart').click();
 await flush(20);
 assert.ok(app.nodes.get('notice').textContent.startsWith('Speech is ready. The GPU couldn'));

 // A failed download keeps the dialog open with the reason and a retry.
 app=boot({progress:[{state:'downloading',stage:'model',done:0,total:1},{state:'error',stage:'model',done:0,total:1,error:'The download stopped. Check your internet connection and try again; it resumes where it left off.'}]});
 await flush();
 n=app.nodes;
 await n.get('setupStart').click();
 await flush(20);
 assert.equal(n.get('setupDialog').open,true);
 assert.equal(n.get('setupError').hidden,false);
 assert.ok(n.get('setupError').textContent.startsWith('The download stopped.'));
 assert.equal(n.get('setupStart').disabled,false);
 assert.equal(n.get('setupStart').textContent,'Try again');

 // Cancel pauses; the next start resumes.
 app=boot({progress:[{state:'downloading',stage:'model',done:1,total:2}]});
 await flush();
 n=app.nodes;
 vm.runInContext('SETUP_POLL=60000',app.context);
 await n.get('setupStart').click();
 await n.get('setupCancel').click();
 await flush();
 assert.equal(app.posted('/api/setup/cancel').length,1);
 assert.equal(n.get('setupProgressText').textContent,'Paused. Download again to resume.');
 assert.equal(n.get('setupStart').disabled,false);

 // Skip: Wispr Flow becomes the dictation choice and the dialog closes.
 app=boot();
 await flush();
 n=app.nodes;
 await n.get('setupSkip').click();
 await flush();
 assert.equal(app.posted('/api/setup/skip').length,1);
 assert.equal(n.get('dictationProvider').value,'wispr');
 assert.equal(app.storage.get('relay-dictation-provider'),'wispr');
 assert.equal(n.get('setupDialog').open,false);

 // Desktop window: the folder picker is offered and a picked model closes setup.
 app=boot({status:{nativeDesktop:true}});
 await flush();
 n=app.nodes;
 assert.equal(n.get('setupPick').hidden,false);
 await n.get('setupPick').click();
 await flush();
 assert.deepEqual(app.posted('/api/whisper-model')[0].body,{path:'C:\\Models\\large-v3'});
 assert.equal(n.get('notice').textContent,'Model folder saved.');
 assert.equal(n.get('setupDialog').open,false);

 // Already set up: no dialog.
 app=boot({status:{setupNeeded:false,speechConfigured:true}});
 await flush();
 assert.notEqual(app.nodes.get('setupDialog').open,true);
 assert.equal(app.posted('/api/setup').length,0);

 // Settings → Transcription: model, device, missing folder.
 const ready={setupNeeded:false,speechConfigured:true,speechModel:'Whisper large-v3',speechDevice:'GPU',modelOwned:false,cpuOnly:false,missingModel:null};
 app=boot({status:ready});
 await flush();
 n=app.nodes;
 assert.equal(n.get('speechModelDescription').textContent,'Model: Whisper large-v3, running on your NVIDIA GPU.');
 assert.equal(n.get('speechStatus').textContent,'Local · Whisper large-v3 · GPU');
 assert.equal(n.get('deleteModel').hidden,true);
 assert.equal(html.match(/id="pickModel"[^>]*>([^<]*)</)[1],'Change folder…');
 app=boot({status:{...ready,speechModel:'Whisper large-v3-turbo',speechDevice:'CPU'}});
 await flush();
 assert.equal(app.nodes.get('speechModelDescription').textContent,'Model: Whisper large-v3-turbo, running on the CPU.');
 assert.equal(app.nodes.get('speechStatus').textContent,'Local · Whisper large-v3-turbo · CPU');
 app=boot({status:{setupNeeded:false,speechConfigured:false}});
 await flush();
 assert.equal(app.nodes.get('speechModelDescription').textContent,'No speech model is set up yet.');
 app=boot({status:{setupNeeded:false,speechConfigured:false,missingModel:'C:\\Models\\large-v3'}});
 await flush();
 assert.equal(app.nodes.get('speechModelDescription').textContent,'Your model folder was moved or deleted: C:\\Models\\large-v3. Change folder or re-download.');

 // Always use CPU saves and refreshes.
 app=boot({status:ready});
 await flush();
 n=app.nodes;
 const statusCalls=app.posted('/api/status').length;
 n.get('cpuOnly').checked=true;await n.get('cpuOnly').listeners.change();await flush();
 assert.deepEqual(app.posted('/api/speech-device')[0].body,{cpuOnly:true});
 assert.ok(app.posted('/api/status').length>statusCalls);

 // Delete only for app-downloaded models, and only after confirming.
 app=boot({status:{...ready,modelOwned:true}});
 await flush();
 assert.equal(app.nodes.get('deleteModel').hidden,false);
 await app.nodes.get('deleteModel').click();await flush();
 assert.equal(app.posted('/api/model/delete').length,1);
 app=boot({status:{...ready,modelOwned:true},confirmReply:false});
 await flush();
 await app.nodes.get('deleteModel').click();await flush();
 assert.equal(app.posted('/api/model/delete').length,0);

 // Re-download reopens setup and replaces the model.
 app=boot({status:ready,progress:[{state:'done',device:'GPU'}]});
 await flush();
 n=app.nodes;
 assert.notEqual(n.get('setupDialog').open,true);
 await n.get('redownloadModel').click();await flush();
 assert.equal(n.get('setupDialog').open,true);
 await n.get('setupStart').click();
 assert.deepEqual(app.posted('/api/setup/download')[0].body,{model:'large-v3-turbo',fresh:true});
 // Once the fresh download has started, Download again after Cancel must resume, not wipe it again.
 app=boot({status:ready,progress:[{state:'downloading',stage:'model',done:1,total:2}]});
 await flush();
 n=app.nodes;
 vm.runInContext('SETUP_POLL=60000',app.context);
 await n.get('redownloadModel').click();await flush();
 await n.get('setupStart').click();
 await n.get('setupCancel').click();await flush();
 await n.get('setupStart').click();
 assert.deepEqual(app.posted('/api/setup/download').map(r=>r.body.fresh),[true,false]);

 console.log('Setup screen: checklist, model choice, download progress, failure, pause, skip, folder pick and Transcription settings verified. No real downloads.');
 process.exit(0);  // stop any leftover poll timer
})().catch(error=>{console.error(error);process.exit(1);});
