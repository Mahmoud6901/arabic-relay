// Runs the real interface script with a fake microphone and mocked network.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const html=fs.readFileSync(require('node:path').join(__dirname,'..','src','arabic_relay','index.html'),'utf8');
const nodes=new Map();
function element(){return {value:'',disabled:false,textContent:'',innerHTML:'',style:{setProperty(){}},dataset:{},classList:{toggle(){},remove(){}},listeners:{},addEventListener(name,fn){this.listeners[name]=fn;},setAttribute(){},removeAttribute(){},querySelector(){return element();},append(){},after(){},remove(){},focus(){this.focused=true;},showModal(){this.open=true;},close(){this.open=false;this.listeners.close?.();},click(){return this.listeners.click?.();}};}
for(const match of html.matchAll(/id="([^"]+)"/g))nodes.set(match[1],element());
const requests=[],storage=new Map(); let fail=false, restoreFailure=false, stops=0,micStarts=0,learningEnabled=true,learningFailure=false;
let transcribeNotice=null,autoSpelling=false,correctionGate=null,releaseCorrection=null,autoRestoreText=null;
class Recorder{
 static isTypeSupported(t){return t==='audio/webm;codecs=opus';}
 constructor(){this.state='inactive';}
 start(){this.state='recording';}
 stop(){this.state='inactive';this.ondataavailable({data:new Blob([new Uint8Array([26,69,223,163,1])])});queueMicrotask(()=>this.onstop());}
}
class MockAudioContext{async decodeAudioData(){return {duration:0.01};}async close(){}}
class MockOfflineAudioContext{createBufferSource(){return {connect(){},start(){}};}async startRendering(){return {getChannelData:()=>new Float32Array([0,-1,1])};}}
const context=vm.createContext({console,crypto:require('node:crypto'),Blob,Map,Set,JSON,encodeURIComponent,queueMicrotask,setTimeout,clearTimeout,MediaRecorder:Recorder,
 AudioContext:MockAudioContext,OfflineAudioContext:MockOfflineAudioContext,
 document:{getElementById:id=>nodes.get(id)||null,createElement:element,documentElement:{dataset:{}},addEventListener(){}},
 localStorage:{getItem(key){return storage.get(key)||'';},setItem(key,value){storage.set(key,value);}},
 navigator:{mediaDevices:{async getUserMedia(){micStarts++;return {getTracks:()=>[{stop(){stops++;}}]};}}},
 URL:{createObjectURL(){return 'blob:mock';},revokeObjectURL(){}},
 fetch:async(url,options)=>{requests.push({url,options});let data;
   if(url==='/api/status')data={csrf:'test-token',connected:false,speechConfigured:true,speechModel:'Whisper large-v3',speechDevice:'GPU',setupNeeded:false,promptChoice:{tool:'codex',model:'',effort:'low'},promptLabel:'Codex · Default · low',selfLearning:learningEnabled,autoSpelling};
   else if(url==='/api/auto-spelling'){autoSpelling=JSON.parse(options.body).enabled;data={autoSpelling};}
   else if(url==='/api/self-learning'){if(learningFailure)data={error:'Settings unavailable'};else{learningEnabled=JSON.parse(options.body).enabled;data={selfLearning:learningEnabled};}}
   else if(url==='/api/catalog')data={projects:[],live:false};
   else if(url==='/api/vocabulary'||url==='/api/forget-vocabulary')data={words:[],corrections:[]};
   else if(url==='/api/translate')data={english:'The deadline is after the meeting. Keep React.',vocabulary:{words:['deadline','meeting','React'],corrections:[{heard:'الديدلاين',spelling:'deadline'}]}};
   else if(url==='/api/send')data={deliveries:[{projectId:'desktop:claude',threadId:'desktop-target',title:'Claude / New chat',state:'submitted',receipt:{delivery:'desktop_submit'}}]};
   else if(url==='/api/wispr/open')data={installed:false};
   else if(url==='/api/cohere/key')data={configured:!!JSON.parse(options.body).key};
   else if(url==='/api/transcribe')data=fail?{error:'mock network failure'}:{text:'ضيف dark mode في React',rawText:'ضيف dark mode في React',notice:transcribeNotice};
   else if(url==='/api/restore-english'){if(correctionGate)await correctionGate;data=restoreFailure?{error:'Codex unavailable'}:{text:autoRestoreText||'الـ deadline بعد الـ meeting وخلي React زي ما هو'};}
   else throw new Error('Unexpected request '+url);
   return {ok:!((url==='/api/transcribe'&&fail)||(url==='/api/restore-english'&&restoreFailure)||(url==='/api/self-learning'&&learningFailure)),json:async()=>data};
 }});
context.window={MediaRecorder:Recorder,addEventListener(){}};
for(const script of html.matchAll(/<script>([\s\S]*?)<\/script>/g))vm.runInContext(script[1],context);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 await flush();
 assert.equal(nodes.get('speechStatus').textContent,'Local · Whisper large-v3 · GPU');
 assert.equal(nodes.get('autoSpelling').checked,false);
 assert.equal(nodes.get('selfLearning').checked,true);
 nodes.get('arabic').value='Existing draft';
 nodes.get('selfLearning').checked=false;await nodes.get('selfLearning').listeners.change();
 assert.equal(nodes.get('selfLearning').checked,false);
 assert.equal(nodes.get('arabic').value,'Existing draft');
 assert.ok(nodes.get('learningStatus').textContent.includes('off'));
 await nodes.get('refresh').click();
 assert.equal(nodes.get('selfLearning').checked,false);
 learningFailure=true;nodes.get('selfLearning').checked=true;await nodes.get('selfLearning').listeners.change();
 assert.equal(nodes.get('selfLearning').checked,false);
 assert.ok(nodes.get('notice').textContent.includes('Settings unavailable'));
 learningFailure=false;nodes.get('selfLearning').checked=true;await nodes.get('selfLearning').listeners.change();
 assert.equal(nodes.get('selfLearning').checked,true);
 await nodes.get('clearDraft').click();
 nodes.get('arabic').value='Draft survives Settings';
 await nodes.get('settingsButton').click();
 assert.equal(nodes.get('settingsDialog').open,true);
 assert.equal(nodes.get('speechDialog').hidden,false);
 await nodes.get('speechClose').click();
 assert.equal(nodes.get('settingsDialog').open,false);
 assert.equal(nodes.get('arabic').value,'Draft survives Settings');
 assert.equal(nodes.get('settingsButton').focused,true);
 await nodes.get('clearDraft').click();
 await nodes.get('speechSetup').click();
 assert.equal(nodes.get('speechDialog').hidden,false);
 await nodes.get('speechClose').click();
 assert.equal(nodes.get('speechDialog').hidden,true);
 assert.ok(!html.includes('<dialog id="speechDialog"'));
 assert.ok(!html.includes('id="speechKey"'));
 // External Flow dictation changes input mode; Relay never captures or uploads its audio.
 nodes.get('dictationProvider').value='wispr';nodes.get('dictationProvider').listeners.change();
 assert.equal(storage.get('relay-dictation-provider'),'wispr');
 assert.equal(nodes.get('record').hidden,true);
 assert.equal(nodes.get('wisprFocus').hidden,false);
 assert.ok(nodes.get('privacySummary').textContent.includes('online'));
 await nodes.get('wisprOpen').click();
 assert.ok(requests.some(r=>r.url==='/api/wispr/open'));
 const externalRequests=requests.length;
 await nodes.get('wisprFocus').click();await nodes.get('record').click();await nodes.get('retryAudio').click();
 assert.equal(nodes.get('arabic').focused,true);
 assert.equal(micStarts,0);
 assert.equal(requests.length,externalRequests);
 nodes.get('arabic').value='نص من Wispr Flow';nodes.get('arabic').listeners.input();
 assert.equal(requests.length,externalRequests);
 assert.equal(nodes.get('send').disabled,true);
 vm.runInContext("speechConfigured=false;update();",context);
 assert.equal(nodes.get('arabic').disabled,false);
 assert.equal(nodes.get('wisprFocus').disabled,false);
 await nodes.get('clearDraft').click();
 assert.equal(nodes.get('dictationProvider').value,'wispr');
 nodes.get('dictationProvider').value='whisper';nodes.get('dictationProvider').listeners.change();
 vm.runInContext("speechConfigured=true;update();",context);
 assert.equal(nodes.get('record').hidden,false);
 assert.equal(nodes.get('speechStatus').textContent,'Local · Whisper large-v3 · GPU');
 // Cohere is an explicit opt-in with encrypted server storage, not browser storage.
 nodes.get('dictationProvider').value='cohere';nodes.get('dictationProvider').listeners.change();
 assert.equal(storage.get('relay-dictation-provider'),'cohere');
 assert.equal(nodes.get('record').hidden,false);
 assert.ok(nodes.get('privacySummary').textContent.includes('online'));
 const beforeCohereMic=micStarts;
 await nodes.get('record').click();
 assert.equal(micStarts,beforeCohereMic);
 assert.equal(nodes.get('speechDialog').hidden,false);
 nodes.get('cohereKey').value='synthetic-api-key';await nodes.get('cohereSave').click();
 assert.equal(nodes.get('cohereKey').value,'');
 assert.ok(![...storage.values()].includes('synthetic-api-key'));
 vm.runInContext('speechConfigured=false;update();',context);
 await nodes.get('record').click();await nodes.get('record').click();await flush();await flush();
 const cohereRequest=requests.find(r=>r.url==='/api/transcribe');
 assert.equal(cohereRequest.options.headers['X-Relay-Provider'],'cohere');
 assert.equal(cohereRequest.options.headers['Content-Type'],'audio/wav');
 const pcm=new DataView(await cohereRequest.options.body.arrayBuffer());
 assert.equal(pcm.getUint32(24,true),16000);assert.equal(pcm.getUint16(22,true),1);
 assert.equal(pcm.getInt16(46,true),-32768);assert.equal(pcm.getInt16(48,true),32767);
 await nodes.get('cohereRemove').click();
 assert.ok(nodes.get('cohereKeyStatus').textContent.includes('No key'));
 await nodes.get('clearDraft').click();requests.splice(requests.findIndex(r=>r===cohereRequest),1);
 nodes.get('dictationProvider').value='whisper';nodes.get('dictationProvider').listeners.change();
 vm.runInContext('speechConfigured=true;update();',context);
 nodes.get('vocabulary').value='React, dark mode';
 await nodes.get('record').click();
 assert.equal(nodes.get('translate').disabled,true);
 assert.equal(nodes.get('refresh').disabled,true);
 assert.equal(nodes.get('clearDraft').disabled,true);
 await nodes.get('record').click();await flush();
 assert.equal(nodes.get('arabic').value,'ضيف dark mode في React');
 assert.equal(requests.filter(r=>r.url==='/api/transcribe').length,1);
 assert.equal(nodes.get('send').disabled,true);
 assert.ok(stops>0);
 await nodes.get('record').click();await nodes.get('cancelRecording').click();await flush();
 assert.equal(requests.filter(r=>r.url==='/api/transcribe').length,1);
 fail=true;await nodes.get('record').click();await nodes.get('record').click();await flush();
 assert.equal(nodes.get('arabic').value,'ضيف dark mode في React');
 assert.equal(nodes.get('voiceRetry').style.display,'flex');
 assert.equal(requests.filter(r=>r.url==='/api/transcribe').length,2);
 await flush();assert.equal(requests.filter(r=>r.url==='/api/transcribe').length,2);
 fail=false;await nodes.get('retryAudio').click();await flush();
 assert.equal(requests.filter(r=>r.url==='/api/transcribe').length,3);
 assert.equal(nodes.get('voiceRetry').style.display,'none');
 assert.equal(requests.filter(r=>r.url==='/api/restore-english').length,0); // Off means no automatic Codex requests.
 nodes.get('arabic').value='الديدلاين بعد الميتنج وخلي React زي ما هو';
 await nodes.get('restoreEnglish').click();await flush();
 assert.equal(nodes.get('arabic').value,'الـ deadline بعد الـ meeting وخلي React زي ما هو');
 assert.equal(nodes.get('originalSpeechText').textContent,'الديدلاين بعد الميتنج وخلي React زي ما هو');
 assert.equal(nodes.get('originalSpeech').style.display,'block');
 restoreFailure=true;const before=nodes.get('arabic').value;
 await nodes.get('restoreEnglish').click();await flush();
 assert.equal(nodes.get('arabic').value,before);
 assert.equal(nodes.get('send').disabled,true);
 await nodes.get('translate').click();await flush();
 const prepared=JSON.parse(requests.find(r=>r.url==='/api/translate').options.body);
 assert.equal(prepared.arabic,before);
 assert.ok(prepared.original.includes('الديدلاين'));
 assert.equal(nodes.get('vocabularyStatus').textContent,'Personal vocabulary: 3 words · 1 correction');
 assert.ok(nodes.get('learnedWords').textContent.includes('deadline'));
 assert.equal(nodes.get('send').disabled,true);
 await nodes.get('forgetVocabulary').click();await flush();
 assert.equal(nodes.get('vocabularyStatus').textContent,'Personal vocabulary: 0 words · 0 corrections');
 assert.equal(nodes.get('arabic').value,before);
 // One Send action becomes available when a live destination and prompt are ready.
 vm.runInContext("connected=true;live=true;selected.set('test-thread',{id:'test-thread',projectId:'project',project:'Project',title:'Session'});update();",context);
 assert.ok(!nodes.has('approve'));
 assert.equal(nodes.get('send').disabled,false);
 vm.runInContext("live=false;update();",context);
 assert.equal(nodes.get('send').disabled,true); // A saved sign-in is not live T3 access.
 vm.runInContext("live=true;busy=true;update();",context);
 assert.equal(nodes.get('dictationProvider').disabled,true);
 assert.equal(nodes.get('autoSpelling').disabled,true);
 nodes.get('dictationProvider').value='wispr';nodes.get('dictationProvider').listeners.change();
 assert.equal(nodes.get('dictationProvider').value,'whisper');
 vm.runInContext("busy=false;update();",context);
 const vocabularyBefore=nodes.get('vocabulary').value;
 await nodes.get('clearDraft').click();await flush();
 assert.equal(nodes.get('arabic').value,'');
 assert.equal(nodes.get('english').value,'');
 assert.equal(nodes.get('originalSpeechText').textContent,'');
 assert.equal(nodes.get('originalSpeech').style.display,'none');
 assert.equal(nodes.get('selectedCount').textContent,1);
 assert.equal(nodes.get('vocabulary').value,vocabularyBefore);
 assert.equal(nodes.get('send').disabled,true);
 assert.ok(nodes.get('send').title.includes('English prompt'));
 assert.ok(!requests.some(r=>r.url.includes('approval')||r.url==='/api/approve'));
 await nodes.get('record').click();await nodes.get('record').click();await flush();
 assert.equal(nodes.get('arabic').value,'ضيف dark mode في React');
 fail=true;await nodes.get('record').click();await nodes.get('record').click();await flush();
 const uploaded=requests.filter(r=>r.url==='/api/transcribe').length;
 await nodes.get('clearDraft').click();await nodes.get('retryAudio').click();await flush();
 assert.equal(requests.filter(r=>r.url==='/api/transcribe').length,uploaded);
 assert.equal(nodes.get('voiceRetry').style.display,'none');
 // With correction enabled, raw text must be visible while Codex is still pending.
 fail=false;restoreFailure=false;
 nodes.get('autoSpelling').checked=true;await nodes.get('autoSpelling').listeners.change();
 await nodes.get('refresh').click();assert.equal(nodes.get('autoSpelling').checked,true);
 autoRestoreText='ضيف dark mode في React بعد التصحيح';
 correctionGate=new Promise(resolve=>{releaseCorrection=resolve;});
 await nodes.get('record').click();await nodes.get('record').click();await flush();
 assert.equal(nodes.get('arabic').value,'ضيف dark mode في React');
 assert.ok(nodes.get('notice').textContent.includes('transcript is visible'));
 assert.equal(nodes.get('originalSpeechText').textContent,'ضيف dark mode في React');
 releaseCorrection();await flush();await flush();correctionGate=null;
 assert.equal(nodes.get('arabic').value,autoRestoreText);
 await nodes.get('clearDraft').click();restoreFailure=true;
 await nodes.get('record').click();await nodes.get('record').click();await flush();await flush();
 assert.equal(nodes.get('arabic').value,'ضيف dark mode في React');
 assert.equal(nodes.get('voiceRetry').style.display,'none');
 assert.ok(nodes.get('notice').textContent.includes('original is preserved'));
 nodes.get('autoSpelling').checked=false;await nodes.get('autoSpelling').listeners.change();
 await nodes.get('refresh').click();assert.equal(nodes.get('autoSpelling').checked,false);
 // A GPU fallback on one recording is announced with the transcript.
 await nodes.get('clearDraft').click();restoreFailure=false;transcribeNotice='The GPU was unavailable, so this recording ran on the CPU.';
 await nodes.get('record').click();await nodes.get('record').click();await flush();await flush();
 assert.ok(nodes.get('notice').textContent.includes(transcribeNotice),nodes.get('notice').textContent);transcribeNotice=null;
 assert.ok(!requests.some(r=>r.url==='/api/send'));
 vm.runInContext("connected=false;selected.clear();selected.set('desktop-target',{id:'desktop-target',projectId:'desktop:claude',project:'Claude Desktop',title:'New chat'});$('english').value='Reviewed prompt.';update();",context);
 assert.equal(nodes.get('send').disabled,false); // Desktop destinations are independent of T3 sign-in.
 await nodes.get('send').click();
 const attempt=requests.filter(r=>r.url==='/api/send');
 assert.equal(attempt.length,1);
 const sentBody=JSON.parse(attempt[0].options.body);
 assert.equal(sentBody.english,'Reviewed prompt.');
 assert.equal(sentBody.targets[0].projectId,'desktop:claude');
 assert.ok(sentBody.requestId);
 assert.equal(nodes.get('send').disabled,true);
 await nodes.get('send').click();assert.equal(requests.filter(r=>r.url==='/api/send').length,1);
 assert.equal(nodes.get('english').disabled,false);
 nodes.get('english').value='New prompt';nodes.get('english').listeners.input();
 assert.equal(nodes.get('send').disabled,false);
 assert.ok([...html.matchAll(/<svg\b[^>]*>/g)].every(m=>m[0].includes('aria-hidden="true"')));
 const submitted=vm.runInContext("deliveryMarkup({projectId:'desktop:claude',threadId:'opaque',title:'Claude / New chat',state:'submitted',receipt:{}})",context);
 assert.ok(submitted.includes('Submitted'));
 assert.ok(!submitted.includes('T3 confirmed'));
 assert.ok(!submitted.includes('t3-thread://'));
 console.log('Immediate transcript display, optional Codex correction, saved preferences, failure preservation and existing providers verified. No real uploads, Codex calls or T3 sends.');
})().catch(e=>{console.error(e);process.exitCode=1;});
