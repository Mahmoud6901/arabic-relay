// Processing during speech in the real interface script: ordered PCM chunks, provisional display, final transcript,
// fallback to the whole recording, cancellation, and automatic paste of the final prompt only.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const html=fs.readFileSync(require('node:path').join(__dirname,'..','src','arabic_relay','index.html'),'utf8');
const quickMode=process.argv.includes('--quick');
const nodes=new Map();
function element(){return {value:'',disabled:false,hidden:false,textContent:'',innerHTML:'',style:{setProperty(){}},dataset:{},classList:{toggle(){},remove(){}},listeners:{},addEventListener(name,fn){this.listeners[name]=fn;},setAttribute(){},removeAttribute(){},querySelector(){return element();},append(){},after(){},remove(){},focus(){this.focused=true;},showModal(){this.open=true;},close(){this.open=false;this.listeners.close?.();},click(){return this.listeners.click?.();}};}
for(const match of html.matchAll(/id="([^"]+)"/g))nodes.set(match[1],element());
const requests=[],storage=new Map(),contexts=[],workletNodes=[],pastes=[],stages=[];
const inlineQuick=quickMode&&process.argv.includes('--inline');
if(inlineQuick)storage.set('relay-writing-mode','inline');
let micStarts=0,stops=0,incremental=true,chunkFailure=false,finishFailure=false,finishGate=null;
class Recorder{
 static isTypeSupported(t){return t==='audio/webm;codecs=opus';}
 constructor(){this.state='inactive';}
 start(){this.state='recording';}
 stop(){this.state='inactive';this.ondataavailable({data:new Blob([new Uint8Array([26,69,223,163,1])])});queueMicrotask(()=>this.onstop());}
}
class MockAudioContext{
 constructor(options){this.sampleRate=options?.sampleRate||48000;this.state='running';this.modules=[];this.audioWorklet={addModule:async url=>{this.modules.push(url);}};contexts.push(this);}
 createMediaStreamSource(stream){return {stream,connected:null,connect(node){this.connected=node;},disconnect(){this.connected=null;}};}
 async close(){this.state='closed';}
 async decodeAudioData(){return {duration:0.01};}
}
class MockWorkletNode{
 constructor(ctx,name,options){this.ctx=ctx;this.name=name;this.options=options;this.messages=[];const node=this;
  this.port={onmessage:null,postMessage(message){node.messages.push(message);if(message==='flush')setTimeout(()=>{node.emit(new Int16Array(4000));node.port.onmessage({data:{type:'flushed'}});});}};
  workletNodes.push(this);}
 emit(samples){this.port.onmessage({data:{type:'pcm',samples}});}
}
const context=vm.createContext({console,crypto:require('node:crypto'),Blob,Map,Set,JSON,Int16Array,encodeURIComponent,queueMicrotask,setTimeout,clearTimeout,MediaRecorder:Recorder,
 AudioContext:MockAudioContext,AudioWorkletNode:MockWorkletNode,
 document:{getElementById:id=>nodes.get(id)||null,createElement:element,documentElement:{dataset:{}},addEventListener(){}},
 localStorage:{getItem(key){return storage.get(key)||'';},setItem(key,value){storage.set(key,value);}},
 navigator:{mediaDevices:{async getUserMedia(){micStarts++;return {getTracks:()=>[{stop(){stops++;}}]};}}},
 URL:{createObjectURL(){return 'blob:mock';},revokeObjectURL(){}},
 fetch:async(url,options)=>{requests.push({url,options});let data,ok=true;
  if(url==='/api/status')data={csrf:'test-token',speechConfigured:true,speechModel:'Whisper large-v3',speechDevice:'GPU',setupNeeded:false,promptChoice:{tool:'codex',model:'',effort:'low'},promptLabel:'Codex · Default · low',selfLearning:true,autoSpelling:false,incrementalDictation:incremental,nativeDesktop:true};
  else if(url==='/api/incremental'){incremental=JSON.parse(options.body).enabled;data={incrementalDictation:incremental};}
  else if(url==='/api/speech/prepare'||url==='/api/speech/cancel-prepare')data={prepared:true};
  else if(url==='/api/vocabulary')data={words:[],corrections:[]};
  else if(url==='/api/stream/start')data={accepted:true};
  else if(url==='/api/stream/chunk'){if(chunkFailure){ok=false;data={error:'Recording audio arrived out of order.'};}else data={seq:Number(options.headers['X-Relay-Stream-Seq']),provisional:'عايز أعمل redesign'};}
  else if(url==='/api/stream/finish'){if(finishGate)await finishGate;if(finishFailure){ok=false;data={error:'This recording is no longer active.'};}else data={text:'عايز أعمل redesign للصفحة',rawText:'عايز أعمل redesign للصفحة',incremental:{used:true,passes:12,finalDecode:'none',tailSeconds:0},timings:{path:'incremental',stopToFinalMs:420}};}
  else if(url==='/api/stream/cancel')data={cancelled:true};
  else if(url==='/api/transcribe')data={text:'نص التسجيل كامل',rawText:'نص التسجيل كامل'};
  else if(url==='/api/translate')data={english:'Redesign the home page.',vocabulary:{words:[],corrections:[]}};
  else if(url==='/api/inline'){const b=JSON.parse(options.body);data={status:'ready',requestId:b.requestId,revision:b.revision,workingText:b.text,outputText:'I want to redesign the page',issues:[],replacements:[]};}
  else if(url==='/api/inline/validate')data={status:'ready',issues:[],proof:{digest:'d'}};
  else throw new Error('Unexpected request '+url);
  return {ok,json:async()=>data};
 }});
const api={begin_capture:async()=>({captureId:'capture-1',available:true,app:'Notepad'}),paste_to_target:async body=>{pastes.push(body);return {status:'paste_requested',message:'Paste requested.'};},finish_paste:async()=>{},shortcut_status:async()=>({available:true}),quick_ready:async()=>{},quick_stage:async stage=>{stages.push(stage);}};
context.window={location:{search:quickMode?'?quick=1':''},MediaRecorder:Recorder,AudioWorkletNode:MockWorkletNode,AudioContext:MockAudioContext,addEventListener(){},pywebview:{api}};
context.confirm=()=>true;
for(const script of html.matchAll(/<script>([\s\S]*?)<\/script>/g))vm.runInContext(script[1],context);
const flush=async(n=3)=>{for(let i=0;i<n;i++)await new Promise(resolve=>setTimeout(resolve,0));};
const count=url=>requests.filter(r=>r.url===url).length;
async function record(){if(quickMode){const pending=context.window.relayQuickToggle();if(nodes.get('draftConfirm').open)nodes.get('draftConfirmAccept').click();await pending;}else await nodes.get('record').click();await flush();}
async function stop(){await nodes.get('stopRecording').click();await flush(8);}
(async()=>{
 await flush();
 if(quickMode){
  // Shortcut capture: provisional text never reaches the paste path; the final prompt pastes exactly once.
  await record();
  workletNodes.at(-1).emit(new Int16Array(16000));await flush();
  assert.ok(nodes.get('captureHint').textContent.includes('redesign'));
  assert.equal(pastes.length,0);
  finishGate=new Promise(resolve=>{context.releaseFinish=resolve;});
  const stopping=stop();await flush();
  assert.equal(pastes.length,0);
  context.releaseFinish();finishGate=null;await stopping;await flush(10);
  assert.equal(count('/api/transcribe'),0);
  assert.equal(pastes.length,1);
  assert.equal(pastes[0].english,inlineQuick?'I want to redesign the page':'Redesign the home page.');
  assert.ok(!pastes[0].english.includes('عايز'));
  assert.ok(!stages.includes('review'),'Stop, transcription, preparation and paste never expand the window');
  // A shortcut recording runs every stage automatically, so its timing names each stage and the total after Stop.
  assert.match(nodes.get('stageStatus').textContent,
   /^Paste requested · transcript \d+\.\d s · prompt \d+\.\d s · paste request \d+\.\d s · total after Stop \d+\.\d s$/,nodes.get('stageStatus').textContent);
  console.log('Quick capture: provisional transcript stays display-only; the final prompt pastes once, never Enter.');
  return;
 }
 assert.equal(nodes.get('incrementalDictation').checked,true);
 // 1. The PCM path starts before the recorder, on the same microphone stream, at 16 kHz.
 await record();
 assert.equal(micStarts,1);
 const ctx=contexts.at(-1),node=workletNodes.at(-1);
 assert.equal(ctx.sampleRate,16000);
 assert.deepEqual(ctx.modules,['/pcm-capture.js']);
 assert.equal(node.options.channelCount,1);
 assert.equal(JSON.parse(requests.find(r=>r.url==='/api/stream/start').options.body).inline,false);
 node.emit(new Int16Array(1600));node.emit(new Int16Array(1600));node.emit(new Int16Array(1600));node.emit(new Int16Array(4000));await flush();
 const chunks=requests.filter(r=>r.url==='/api/stream/chunk');
 assert.deepEqual(chunks.map(r=>r.options.headers['X-Relay-Stream-Seq']),['0','1']);
 assert.ok(chunks.every(r=>r.options.headers['Content-Type']==='application/octet-stream'&&true));
 assert.deepEqual(chunks.map(r=>r.options.body.length),[4800,4000]);   // 100 ms frames batched to a quarter second
 assert.equal(nodes.get('provisionalText').hidden,false);
 assert.ok(nodes.get('provisionalText').textContent.startsWith('Provisional'));
 assert.equal(nodes.get('arabic').value,'');   // provisional text never enters the editable source
 // 2. Stop flushes the tail, then finishes with an exact sample count; no second upload.
 await stop();
 assert.ok(node.messages.includes('flush'));
 const finish=JSON.parse(requests.find(r=>r.url==='/api/stream/finish').options.body);
 assert.deepEqual([finish.lastSeq,finish.totalSamples],[2,12800]);
 assert.equal(count('/api/transcribe'),0);
 assert.equal(nodes.get('arabic').value,'عايز أعمل redesign للصفحة');
 assert.equal(nodes.get('provisionalText').hidden,true);
 assert.equal(ctx.state,'closed');
 assert.equal(count('/api/translate'),0);   // no prompt-model call per partial or per transcript in the workspace
 // 3. A failed chunk falls back to uploading the whole recording and cancels the server job.
 await nodes.get('clearDraft').click();chunkFailure=true;
 await record();workletNodes.at(-1).emit(new Int16Array(16000));await flush();
 await stop();
 assert.equal(count('/api/stream/cancel'),1);
 assert.equal(count('/api/transcribe'),1);
 assert.equal(nodes.get('arabic').value,'نص التسجيل كامل');
 chunkFailure=false;
 // 4. A finish failure also falls back; the recording is never lost.
 await nodes.get('clearDraft').click();finishFailure=true;
 await record();workletNodes.at(-1).emit(new Int16Array(16000));await flush();await stop();
 assert.equal(count('/api/transcribe'),2);
 finishFailure=false;
 // 5. Cancel discards the job: no finish, no upload, provisional cleared, capture closed.
 await nodes.get('clearDraft').click();
 const finishes=count('/api/stream/finish'),cancels=count('/api/stream/cancel');
 await record();workletNodes.at(-1).emit(new Int16Array(16000));await flush();
 await nodes.get('cancelRecording').click();await flush(5);
 assert.equal(count('/api/stream/finish'),finishes);
 assert.equal(count('/api/stream/cancel'),cancels+1);
 assert.equal(count('/api/transcribe'),2);
 assert.equal(nodes.get('provisionalText').hidden,true);
 assert.equal(contexts.at(-1).state,'closed');
 // 6. Inline mode sends inline=true and receives the finished transcript only.
 await nodes.get('inlineMode').click();await nodes.get('clearDraft').click();
 await record();
 assert.equal(JSON.parse(requests.filter(r=>r.url==='/api/stream/start').at(-1).options.body).inline,true);
 workletNodes.at(-1).emit(new Int16Array(16000));await flush();
 const inlineBefore=count('/api/inline');
 await stop();await flush(10);
 assert.equal(count('/api/inline'),inlineBefore+1);
 assert.equal(nodes.get('english').value,'I want to redesign the page');
 await nodes.get('craftMode').click();
 // 7. Turning the experiment off uses only the existing recorder path.
 nodes.get('incrementalDictation').checked=false;await nodes.get('incrementalDictation').listeners.change();
 assert.equal(incremental,false);
 const contextCount=contexts.length;await nodes.get('clearDraft').click();
 await record();await stop();
 assert.equal(contexts.length,contextCount);
 assert.equal(count('/api/transcribe'),3);
 assert.equal(micStarts,6);   // one microphone stream per recording, never an extra one
 console.log('Processing during speech: ordered chunks, provisional display, final transcript, fallback, cancellation and opt-out verified.');
})().catch(e=>{console.error(e);process.exitCode=1;});
