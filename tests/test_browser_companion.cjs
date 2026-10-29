// Run the shipped companion scripts with fake Chrome transport and editors. No external sends.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const dir=path.join(__dirname,'../src/arabic_relay/browser_companion');
const address=require(path.join(dir,'shared.js'));
const url='https://chatgpt.com/c/12345678-1234-4234-8234-123456789abc';
assert.equal(address.parse(url).id,address.parse(url.replace('/c/','/g/project/c/')).id);
for(const bad of [url+'?x=1',url+'#x',url.replace('chatgpt.com','evil.test'),url.replace('https://','https://user@'),'https://claude.ai/new'])assert.equal(address.parse(bad),null);
const code='https://claude.ai/code/session_01ExampleCodeSessionABC1';
assert.notEqual(address.parse(code).id,address.parse(code.toLowerCase()).id);

function page({draft='',changeUrl=false,changeText=false,provider='chatgpt'}={}){
 let listener,clicks=0,inserts=0;
 const location={hostname:provider==='chatgpt'?'chatgpt.com':'claude.ai',href:provider==='chatgpt'?url:url.replace('chatgpt.com/c/','claude.ai/chat/')};
 const editor={innerText:draft,getClientRects:()=>[{}],closest:()=>null,focus(){}};
 const button={disabled:false,getClientRects:()=>[{}],closest:()=>null,getAttribute:name=>name==='aria-label'?(provider==='chatgpt'?'Send':'Send message'):null,click(){clicks++;}};
 const document={title:'Test chat',body:{},querySelectorAll(selector){if(selector==='button')return [button];if(selector.includes('contenteditable'))return [editor];return [];},
  execCommand(action,unused,text){assert.equal(action,'insertText');inserts++;editor.innerText=changeText?'Changed':text;if(changeUrl)location.href=url.replace('123456789abc','123456789def');return true;}};
 const context=vm.createContext({URL,Map,module:undefined,location,document,Date,Error,Promise,
  setTimeout:fn=>{queueMicrotask(fn);return 1;},clearTimeout(){},setInterval(){},MutationObserver:class{observe(){}},
  chrome:{runtime:{id:'test',sendMessage:async()=>{},onMessage:{addListener(fn){listener=fn;}}}}});
 vm.runInContext(fs.readFileSync(path.join(dir,'shared.js'),'utf8'),context);
 vm.runInContext(fs.readFileSync(path.join(dir,'content.js'),'utf8'),context);
 return {editor,location,get clicks(){return clicks;},get inserts(){return inserts;},send:command=>new Promise(resolve=>listener({type:'send',command},{id:'test'},resolve))};
}
async function worker(){
 const local={token:'paired'},session={},calls=[],submissions=[],created=[];
 const bucket=data=>({async get(key){return {[key]:data[key]};},async set(value){Object.assign(data,value);},async setAccessLevel(){}});
 const command={id:'one-request',url,text:'Reviewed prompt.',expires:Date.now()/1000+60};
 const context=vm.createContext({URL,Date,Error,Promise,AbortSignal,console,setTimeout:fn=>{queueMicrotask(fn);return 1;},
  fetch:async(endpoint,options)=>{calls.push({endpoint,body:JSON.parse(options.body)});return {ok:true,json:async()=>endpoint.endsWith('/sync')?{commands:[command]}:{received:true}};},
  chrome:{storage:{local:bucket(local),session:bucket(session)},alarms:{create(){},onAlarm:{addListener(){}}},
    runtime:{getURL:p=>'chrome-extension://test/'+p,onMessage:{addListener(){}}},
    tabs:{query:async()=>[{id:10}],get:async()=>({status:'complete',url}),create:async args=>{created.push(args);return{id:11};},sendMessage:async(id,msg)=>{if(msg.type==='discover')return{chats:[{url,title:'Chat'}]};submissions.push(msg);return{submitted:true,url};}}}});
 context.importScripts=filename=>vm.runInContext(fs.readFileSync(path.join(dir,filename),'utf8'),context);
 vm.runInContext(fs.readFileSync(path.join(dir,'background.js'),'utf8'),context);
 await vm.runInContext('sync()',context);await vm.runInContext('sync()',context);
 assert.equal(created.length,1);assert.equal(submissions.length,1);
 assert.equal(created[0].url,url);assert.equal(created[0].active,false);
 assert.equal(calls.filter(x=>x.endpoint.endsWith('/result')).length,1);
 assert.equal(calls.find(x=>x.endpoint.endsWith('/result')).body.submitted,true);
}
(async()=>{
 for(const provider of ['chatgpt','claude']){
  const p=page({provider});const result=await p.send({url:p.location.href,text:'Reviewed\nprompt.',expires:Date.now()/1000+60});
  assert.equal(result.submitted,true);assert.equal(p.clicks,1);assert.equal(p.inserts,1);
 }
 for(const options of [{draft:'Keep my draft'},{changeUrl:true},{changeText:true}]){
  const p=page(options),result=await p.send({url,text:'Reviewed.',expires:Date.now()/1000+60});
  assert.ok(result.error);assert.equal(p.clicks,0);if(options.draft){assert.equal(p.editor.innerText,options.draft);assert.equal(p.inserts,0);}
 }
 let p=page();assert.ok((await p.send({url,text:'Reviewed.',expires:0})).error);assert.equal(p.inserts,0);
 p=page();assert.ok((await p.send({url:url+'-wrong',text:'Reviewed.',expires:Date.now()/1000+60})).error);assert.equal(p.inserts,0);
 await worker();
 console.log('Browser companion: URL identity, draft preservation, destination changes, expired sends, and worker replay guards passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
