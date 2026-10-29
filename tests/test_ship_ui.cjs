// Runs the real interface script against scripted replies: crash notice, Copy diagnostics, About.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const html=fs.readFileSync(require('node:path').join(__dirname,'..','src','arabic_relay','index.html'),'utf8');
const flush=async(times=8)=>{for(let i=0;i<times;i++)await new Promise(resolve=>setTimeout(resolve,2));};

function boot({status={},replies={}}={}){
 const nodes=new Map(),requests=[],clipboard=[],opened=[],calls=[];
 function element(){return {value:'',disabled:false,hidden:false,checked:false,textContent:'',innerHTML:'',style:{setProperty(){}},dataset:{},classList:{toggle(){},remove(){},add(){}},listeners:{},addEventListener(name,fn){this.listeners[name]=fn;},dispatchEvent(event){return this.listeners[event.type]?.(event);},setAttribute(){},removeAttribute(){},querySelector(){return element();},append(){},after(){},remove(){},focus(){},showModal(){this.open=true;},close(){this.open=false;this.listeners.close?.();},click(){return this.listeners.click?.();}};}
 for(const match of html.matchAll(/id="([^"]+)"/g))nodes.set(match[1],element());
 for(const match of html.matchAll(/<[^>]*\sid="([^"]+)"[^>]*>/g))if(/\shidden[\s>]/.test(match[0]))nodes.get(match[1]).hidden=true;
 const context=vm.createContext({console,Blob,Map,Set,JSON,Event:class{constructor(type){this.type=type;}},encodeURIComponent,decodeURIComponent,queueMicrotask,setTimeout,clearTimeout,
  MediaRecorder:class{static isTypeSupported(){return true;}},
  document:{getElementById:id=>nodes.get(id)||null,createElement:element,documentElement:{dataset:{}},addEventListener(){}},
  localStorage:{getItem(){return '';},setItem(){}},
  navigator:{mediaDevices:{},clipboard:{writeText:async text=>{clipboard.push(text);}}},confirm:()=>true,
  fetch:async(url,options)=>{const body=options?.body?JSON.parse(options.body):undefined;requests.push({url,body});let data,ok=true;
   if(url==='/api/status')data={csrf:'t',connected:false,selfLearning:true,autoSpelling:false,speechConfigured:true,setupNeeded:false,
     speechModel:'Whisper large-v3',speechDevice:'GPU',promptChoice:{tool:'codex',model:'',effort:'low'},promptLabel:'Codex · Default · low',
     missingTools:{},version:'0.1.0',crashed:false,update:{mode:'auto',installOnQuit:true,available:null,ready:null,failed:null,releaseUrl:'https://github.com/Mahmoud6901/arabic-relay/releases'},...status};
   else if(url==='/api/catalog')data={projects:[],live:true};
   else if(url==='/api/vocabulary')data={words:[],corrections:[]};
   else if(url in replies){const reply=replies[url];if(reply.error){ok=false;data={error:reply.error};}else data=reply;}
   else data={};
   return {ok,json:async()=>data};}});
 context.window={MediaRecorder:context.MediaRecorder,addEventListener(){},open:(url)=>{opened.push(url);},
  pywebview:{api:{pick_file:async()=>null,pick_folder:async()=>null,quit:async token=>{calls.push(['quit',token]);}}}};
 for(const script of html.matchAll(/<script>([\s\S]*?)<\/script>/g))vm.runInContext(script[1],context);
 return {nodes,requests,clipboard,opened,calls,context,posted:url=>requests.filter(r=>r.url===url)};
}

(async()=>{
 // A clean start: no crash notice; About shows the version.
 let app=boot();
 await flush();
 let n=app.nodes;
 assert.equal(n.get('crashBanner').hidden,true);
 assert.equal(n.get('appVersion').textContent,'Arabic Relay 0.1.0');

 // Direct setup refreshes selectable coding sessions without altering or sending the draft.
 const codingProject={id:'desktop:direct:claude',title:'Claude Code',desktop:true,connectionStatus:'Connected',
  threads:[{id:'desktop:direct:claude:session',title:'Existing coding task',disabled:false,status:'ready',runtimeMode:'Local'}]};
 app=boot({replies:{'/api/direct/claude/connect':{message:'Reopen existing sessions to connect.'},
  '/api/direct/catalog':{projects:[codingProject],apps:[{name:'Claude Code',ready:true,message:'Connected'}]}}});
 await flush();
 n=app.nodes;
 n.get('arabic').value='Keep my source';n.get('english').value='Keep my draft';
 await n.get('connectClaude').click();await flush();
 assert.equal(app.posted('/api/direct/claude/connect').length,1);
 assert.ok(n.get('projectList').innerHTML.includes('Existing coding task'));
 assert.equal(n.get('arabic').value,'Keep my source');assert.equal(n.get('english').value,'Keep my draft');
 assert.equal(n.get('notice').textContent,'Reopen existing sessions to connect.');
 assert.equal(app.posted('/api/send').length,0);
 assert.equal(n.has('pairBrowser'),false);

 // After a crash: the notice, Copy diagnostics, Dismiss.
 app=boot({status:{crashed:true},replies:{'/api/diagnostics':{text:'Arabic Relay 0.1.0\nGPU: none found'},'/api/crash/dismiss':{crashed:false}}});
 await flush();
 n=app.nodes;
 assert.equal(n.get('crashBanner').hidden,false);
 assert.ok(html.includes('<span id="crashText">Arabic Relay closed unexpectedly last time. Copy diagnostics to include them in a GitHub issue.</span>'));
 await n.get('copyCrash').click();await flush();
 assert.equal(app.posted('/api/diagnostics').length,1);
 assert.deepEqual(app.clipboard,['Arabic Relay 0.1.0\nGPU: none found']);
 assert.equal(n.get('notice').textContent,'Diagnostics copied. They contain no transcripts or audio.');
 await n.get('dismissCrash').click();await flush();
 assert.equal(app.posted('/api/crash/dismiss').length,1);
 assert.equal(n.get('crashBanner').hidden,true);

 // Settings → About copies the same report.
 await n.get('copyDiagnostics').click();await flush();
 assert.equal(app.posted('/api/diagnostics').length,2);
 assert.equal(app.clipboard.length,2);

 // Updates: ready → Restart and update installs, then the window quits.
 const url='https://github.com/Mahmoud6901/arabic-relay/releases/tag/v0.2.0';
 const update=extra=>({mode:'auto',installOnQuit:true,available:'0.2.0',ready:null,failed:null,releaseUrl:url,...extra});
 app=boot({status:{update:update({ready:'0.2.0'})},replies:{'/api/update/install':{installing:'0.2.0'}}});
 await flush();
 n=app.nodes;
 assert.equal(n.get('updateBanner').hidden,false);
 assert.equal(n.get('updateText').textContent,'Arabic Relay 0.2.0 is ready');
 assert.equal(n.get('updateNotes').href,url);
 assert.equal(n.get('updateInstall').textContent,'Restart and update');
 assert.equal(n.get('updateInstallItem').hidden,false);
 assert.equal(n.get('updateManualItem').hidden,true);
 assert.ok(html.includes('<span id="updateNotesItem"> · <a class="text-btn" id="updateNotes" target="_blank" rel="noopener">What\'s new</a></span>'));
 await n.get('updateInstall').click();await flush();
 assert.equal(app.posted('/api/update/install').length,1);
 assert.deepEqual(app.calls,[['quit','t']]);

 // While recording the button is disabled.
 vm.runInContext('listening=true;update();',app.context);
 assert.equal(n.get('updateInstall').disabled,true);
 vm.runInContext('listening=false;update();',app.context);
 assert.equal(n.get('updateInstall').disabled,false);

 // The server refuses while busy: its message shows; the window stays.
 app=boot({status:{update:update({ready:'0.2.0'})},replies:{'/api/update/install':{error:'Finish recording, transcribing, translating or sending first, then update.'}}});
 await flush();
 await app.nodes.get('updateInstall').click();await flush();
 assert.equal(app.nodes.get('notice').textContent,'Finish recording, transcribing, translating or sending first, then update.');
 assert.deepEqual(app.calls,[]);

 // Notify only: announced, no install button.
 app=boot({status:{update:update({mode:'notify'})}});
 await flush();
 n=app.nodes;
 assert.equal(n.get('updateText').textContent,'Arabic Relay 0.2.0 is available');
 assert.equal(n.get('updateInstallItem').hidden,true);
 assert.equal(n.get('updateNotesItem').hidden,false);

 // A failed install: Retry and Download manually.
 app=boot({status:{update:update({available:null,failed:'0.2.0',ready:'0.2.0'})},replies:{'/api/update/install':{installing:'0.2.0'}}});
 await flush();
 n=app.nodes;
 assert.equal(n.get('updateText').textContent,"The update to 0.2.0 didn't install.");
 assert.equal(n.get('updateInstall').textContent,'Retry');
 assert.equal(n.get('updateNotesItem').hidden,true);
 assert.equal(n.get('updateManualItem').hidden,false);
 assert.equal(n.get('updateManual').href,url);
 await n.get('updateInstall').click();await flush();
 assert.equal(app.posted('/api/update/install').length,1);

 // Nothing to report: no banner. Settings → Updates saves both preferences.
 app=boot({status:{update:update({available:null})},replies:{'/api/update/preferences':update({available:null,mode:'off',installOnQuit:false})}});
 await flush();
 n=app.nodes;
 assert.equal(n.get('updateBanner').hidden,true);
 assert.equal(n.get('updateMode').value,'auto');
 assert.equal(n.get('installOnQuit').checked,true);
 n.get('updateMode').value='off';await n.get('updateMode').listeners.change();await flush();
 assert.deepEqual(app.posted('/api/update/preferences').at(-1).body,{mode:'off',installOnQuit:true});
 n.get('installOnQuit').checked=false;await n.get('installOnQuit').listeners.change();await flush();
 assert.deepEqual(app.posted('/api/update/preferences').at(-1).body,{mode:'off',installOnQuit:false});
 assert.ok(html.includes('<option value="auto">Automatic</option><option value="notify">Notify only</option><option value="off">Off</option>'));
 assert.ok(html.includes('Install updates automatically when I quit'));

 console.log('ship UI tests passed');
 process.exit(0);
})().catch(error=>{console.error(error);process.exit(1);});
