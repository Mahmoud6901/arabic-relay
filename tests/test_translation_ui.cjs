// Runs the real interface script against scripted replies: Models → English prompt (tool, model, effort, Browse…).
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const html=fs.readFileSync(require('node:path').join(__dirname,'..','src','arabic_relay','index.html'),'utf8');
const flush=async(times=8)=>{for(let i=0;i<times;i++)await new Promise(resolve=>setTimeout(resolve,2));};
const SOL={id:'gpt-6.1-sol',name:'GPT-6.1-Sol',efforts:['low','medium','high','xhigh','max']};
const LUNA={id:'gpt-6-luna',name:'GPT-6-Luna',efforts:['low','medium']};
const CLAUDE=['fable','opus','sonnet','haiku'].map(id=>({id,name:id[0].toUpperCase()+id.slice(1),efforts:['low','medium','high','xhigh','max']}));
const EFFORTS=['low','medium','high','xhigh','max'];
function catalog({codex={},claude={}}={}){return {codex:{installed:true,path:'C:\\Tools\\codex.exe',signedIn:true,models:[SOL,LUNA],...codex},
 claude:{installed:true,path:'C:\\Tools\\claude.exe',signedIn:null,models:CLAUDE,...claude},efforts:EFFORTS};}
const label=c=>`${c.tool==='codex'?'Codex':'Claude Code'} · ${c.model||'Default'} · ${c.effort}`;

function boot({status={},translators=catalog(),choiceError=null,translate=null,inlineHandler=null,storedMode=null,quick=false,provider=null,catalogHandler=null,localTranslator=null}={}){
 const nodes=new Map(),requests=[],storage=new Map();if(storedMode)storage.set('relay-writing-mode',storedMode);
 if(provider)storage.set('relay-dictation-provider',provider);
 function element(){return {value:'',disabled:false,hidden:false,checked:false,textContent:'',innerHTML:'',style:{setProperty(){}},dataset:{},classList:{toggle(){},remove(){},add(){}},listeners:{},addEventListener(name,fn){this.listeners[name]=fn;},dispatchEvent(event){return this.listeners[event.type]?.(event);},setAttribute(){},removeAttribute(){},querySelector(){return element();},append(){},after(){},remove(){},focus(){this.focused=true;},showModal(){this.open=true;},close(){this.open=false;this.listeners.close?.();},click(){return this.listeners.click?.();}};}
 for(const match of html.matchAll(/id="([^"]+)"/g))nodes.set(match[1],element());
 for(const match of html.matchAll(/<[^>]*\sid="([^"]+)"[^>]*>/g))if(/\shidden[\s>]/.test(match[0]))nodes.get(match[1]).hidden=true;
 let choice={tool:'codex',model:'gpt-6.1-sol',effort:'low'};
 const context=vm.createContext({console,Blob,Map,Set,JSON,crypto:require('node:crypto').webcrypto,Event:class{constructor(type){this.type=type;}},encodeURIComponent,decodeURIComponent,queueMicrotask,setTimeout,clearTimeout,
  MediaRecorder:class{static isTypeSupported(){return true;}},
  document:{getElementById:id=>nodes.get(id)||null,createElement:element,documentElement:{dataset:{}},addEventListener(){}},
  localStorage:{getItem(key){return storage.get(key)||'';},setItem(key,value){storage.set(key,value);}},
  navigator:{mediaDevices:{}},confirm:()=>true,
  fetch:async(url,options)=>{const body=options?.body?JSON.parse(options.body):undefined;requests.push({url,body});let data,ok=true;
   if(url==='/api/status')data={csrf:'t',connected:false,selfLearning:true,autoSpelling:false,speechConfigured:true,setupNeeded:false,
     speechModel:'Whisper large-v3',speechDevice:'GPU',promptChoice:choice,promptLabel:label(choice),missingTools:{},...status};
   else if(url==='/api/catalog')data=catalogHandler?await catalogHandler():{projects:[],live:false};
   else if(url==='/api/vocabulary')data={words:[],corrections:[]};
   else if(url==='/api/translators')data={...translators,choice,label:label(choice)};
   else if(url==='/api/setup')data={models:{},recommended:'large-v3'};
   else if(url==='/api/setup/progress')data={state:'idle'};
   else if(url==='/api/prompt-choice'){if(choiceError){ok=false;data={error:choiceError};}else{choice=body;data={promptChoice:choice,promptLabel:label(choice)};}}
   else if(url==='/api/tool-path')data={kind:body.kind,path:body.path};
   else if(url==='/api/translate')data=translate;
   else if(url.startsWith('/api/inline'))data=await inlineHandler?.(url,body)||{};
   else if(url==='/api/local-translator')data={...localTranslator,...body};
   else if(url==='/api/send')data={deliveries:[]};
   else data={};
   return {ok,json:async()=>data};}});
 context.window={location:{search:quick?'?quick=1':''},MediaRecorder:context.MediaRecorder,addEventListener(){},pywebview:{api:{pick_file:async()=>'C:\\Tools\\other\\codex.exe',pick_folder:async()=>null,quick_ready:async()=>{},begin_capture:async()=>({captureId:'capture-test',available:false}),paste_to_target:async(body)=>{requests.push({url:'native:paste',body});return {status:'paste_requested',message:'Paste requested'};},finish_paste:async()=>{},shortcut_status:async()=>({available:true})}}};
 for(const script of html.matchAll(/<script>([\s\S]*?)<\/script>/g))vm.runInContext(script[1],context);
 return {nodes,requests,context,posted:url=>requests.filter(r=>r.url===url),choice:()=>choice};
}
async function openSettings(app){await app.nodes.get('modelsButton').click();await flush();}
const choose=(app,dataset)=>app.nodes.get('modelSettings').listeners.click({target:{closest:()=>({disabled:false,dataset})}});
const pressed=(rows,model)=>rows.includes(`data-prompt-model="${model}" aria-pressed="true"`);

module.exports={boot,flush};
if(require.main===module)(async()=>{
 // The label comes from status, before the Models tab ever opens.
 let app=boot();
 await flush();
 let n=app.nodes;
 assert.equal(n.get('promptChoiceLabel').textContent,'Codex · gpt-6.1-sol · low');
 assert.equal(app.posted('/api/translators').length,0);

 // Opening Models loads the live list: tools, models (Default … another…), efforts.
 await openSettings(app);
 assert.equal(app.posted('/api/translators').length,1);
 let rows=n.get('codexModels').innerHTML;
 assert.ok(rows.indexOf('الافتراضي')<rows.indexOf('>GPT-6.1-Sol<'));
 assert.ok(rows.indexOf('>GPT-6-Luna<')<rows.indexOf('نموذج تاني…'));
 assert.ok(pressed(rows,'gpt-6.1-sol'),'the saved model is the lit row');
 assert.ok(rows.includes('المستخدم حاليًا'));
 assert.equal([...n.get('promptEffort').innerHTML.matchAll(/value="([^"]+)"/g)].map(m=>m[1]).join(','),'low,medium,high,xhigh,max');
 assert.ok(!n.get('promptEffort').innerHTML.includes('ultra'));
 assert.ok(n.get('promptEffort').innerHTML.includes('>منخفض<'));
 assert.equal(n.get('codexStatus').textContent,'مسجّل الدخول');
 assert.equal(n.get('toolHelp').hidden,true);

 // Changing the model saves the choice and updates the label; an unsupported effort moves to the model's first.
 n.get('promptEffort').value='high';await n.get('promptEffort').listeners.change();await flush();
 assert.deepEqual(app.posted('/api/prompt-choice').at(-1).body,{tool:'codex',model:'gpt-6.1-sol',effort:'high'});
 assert.equal(n.get('promptChoiceLabel').textContent,'Codex · gpt-6.1-sol · high');
 await choose(app,{promptTool:'codex',promptModel:'gpt-6-luna'});await flush();
 assert.deepEqual(app.posted('/api/prompt-choice').at(-1).body,{tool:'codex',model:'gpt-6-luna',effort:'low'});
 assert.equal([...n.get('promptEffort').innerHTML.matchAll(/value="([^"]+)"/g)].map(m=>m[1]).join(','),'low,medium');
 assert.ok(pressed(n.get('codexModels').innerHTML,'gpt-6-luna'));

 // Choosing a row of the other tool moves to it; Default is the first of its rows.
 await choose(app,{promptTool:'claude',promptModel:''});await flush();
 assert.deepEqual(app.posted('/api/prompt-choice').at(-1).body,{tool:'claude',model:'',effort:'low'});
 assert.ok(['الافتراضي','>Fable<','>Opus<','>Sonnet<','>Haiku<','نموذج تاني…'].every(t=>n.get('claudeModels').innerHTML.includes(t)));
 assert.ok(!n.get('codexModels').innerHTML.includes('المستخدم حاليًا'),'one model in use across both tools');
 assert.equal(n.get('promptChoiceLabel').textContent,'Claude Code · Default · low');

 // Another model: a text box; Enter saves it.
 await choose(app,{promptTool:'claude',promptCustom:'1'});await flush();
 assert.equal(n.get('promptCustom').hidden,false);
 n.get('promptCustom').value=' claude-opus-5-5 ';await n.get('promptCustom').listeners.keydown({key:'Enter',preventDefault(){}});await flush();
 assert.deepEqual(app.posted('/api/prompt-choice').at(-1).body,{tool:'claude',model:'claude-opus-5-5',effort:'low'});
 assert.equal(n.get('promptCustom').hidden,true);
 assert.ok(n.get('claudeModels').innerHTML.includes('>claude-opus-5-5<'));

 // A refused choice keeps the previous one and says why.
 app=boot({choiceError:'اسم النموذج لازم يكون حروف وأرقام ونقط وشرطات، لحد 80 حرف.'});
 await flush();n=app.nodes;await openSettings(app);
 await choose(app,{promptTool:'codex',promptCustom:'1'});
 n.get('promptCustom').value='bad name';await n.get('promptCustom').listeners.keydown({key:'Enter',preventDefault(){}});await flush();
 assert.ok(pressed(n.get('codexModels').innerHTML,'gpt-6.1-sol'));
 assert.equal(n.get('notice').textContent,'اسم النموذج لازم يكون حروف وأرقام ونقط وشرطات، لحد 80 حرف.');

 // Missing and signed-out tools.
 app=boot({translators:catalog({codex:{signedIn:false},claude:{installed:false,path:null}})});
 await flush();n=app.nodes;await openSettings(app);
 assert.equal(n.get('codexStatus').textContent,'مش مسجّل الدخول. افتح الطرفية واكتب codex login.');
 assert.equal(n.get('claudeStatus').textContent,'مش موجود على الجهاز');
 assert.ok(n.get('claudeModels').innerHTML.includes(' disabled>'),"a missing tool's rows cannot be chosen");
 app=boot({translators:catalog({codex:{installed:false,path:null,models:[],signedIn:null},claude:{installed:false,path:null}})});
 await flush();n=app.nodes;await openSettings(app);
 assert.equal(n.get('toolHelp').hidden,false);
 const help=html.match(/id="toolHelp"[^>]*>([\s\S]*?)<\/p>/)[1];
 assert.ok(help.startsWith('ثبّت واحد منهم عشان تجهيز البرومبت يشتغل:'));
 assert.ok(help.includes('href="https://developers.openai.com/codex/cli"')&&help.includes('href="https://code.claude.com/docs/en/overview"'));

 // A model list that failed still offers Default and another model.
 app=boot({translators:catalog({codex:{models:[]}})});
 await flush();n=app.nodes;await openSettings(app);
 assert.ok(n.get('codexModels').innerHTML.includes('الافتراضي')&&n.get('codexModels').innerHTML.includes('نموذج تاني…'));

 // Browse… and Locate Wispr Flow… (desktop window only).
 assert.equal(n.get('codexBrowse').hidden,true);
 app=boot({status:{nativeDesktop:true}});
 await flush();n=app.nodes;await openSettings(app);
 assert.equal(n.get('codexBrowse').hidden,false);
 await n.get('codexBrowse').click();await flush();
 assert.deepEqual(app.posted('/api/tool-path')[0].body,{kind:'codex',path:'C:\\Tools\\other\\codex.exe'});
 assert.equal(app.posted('/api/translators').length,2);
 await n.get('wisprLocate').click();await flush();
 assert.deepEqual(app.posted('/api/tool-path')[1].body,{kind:'wispr',path:'C:\\Tools\\other\\codex.exe'});

 // A moved Browse… choice is reported.
 app=boot({status:{missingTools:{codex:'C:\\Tools\\codex.exe'}}});
 await flush();
 assert.equal(app.nodes.get('notice').textContent,'Codex اتنقل أو اتمسح: C:\\Tools\\codex.exe. البرنامج هيستخدم أي نسخة يلاقيها، أو اختار مكانه من تبويب النماذج.');

 // A model fallback during Prepare is shown.
 app=boot({translate:{english:'Done.',notice:'gpt-old is no longer available, so Arabic Relay switched to the default model.'}});
 await flush();n=app.nodes;
 n.get('arabic').value='نص';await n.get('translate').click();await flush();
 assert.equal(n.get('english').value,'Done.');
 assert.ok(n.get('notice').textContent.startsWith('gpt-old is no longer available'));

 // The old single Model route is gone from the page.
 assert.ok(!html.includes('/api/prompt-model'));
 console.log('English prompt settings: tool, model, effort, Custom…, sign-in lines, Browse…, missing tools and fallback notice verified. No real tools run.');
 process.exit(0);
})().catch(error=>{console.error(error);process.exit(1);});
