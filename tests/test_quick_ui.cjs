// Real capture preparation and automatic native delivery, with fake OS and network calls.
const assert=require('node:assert/strict');
const vm=require('node:vm');
const {boot,flush}=require('./test_translation_ui.cjs');
async function ready(app){await flush();await flush();await flush();}
function native(app,{failure=false,available=true}={}){
 const calls=[];
 Object.assign(app.context.window.pywebview.api,{
  begin_capture:async()=>({captureId:'one-recording',available,app:'Notepad'}),
  paste_to_target:async(body)=>{calls.push(body);if(failure)throw Error('Original input focus changed.');return {status:'paste_requested',message:'Paste shortcut sent. Enter was not pressed.'};},
  finish_paste:async()=>{calls.push('closed');}
 });return calls;
}
(async()=>{
 let app=boot({quick:true,provider:'wispr',status:{nativeDesktop:true},translate:{english:'Improve the login page.'}});
 let calls=native(app);await ready(app);let n=app.nodes;
 assert.equal(n.has('toggleRecipients'),false);assert.equal(n.has('sidebar'),false);
 assert.equal(n.has('connectCodex'),false);assert.equal(n.has('connect'),false);
 await app.context.window.relayQuickToggle();
 assert.equal(n.get('flowCapture').focused,true);
 n.get('flowCapture').value='حسن صفحة الدخول';n.get('flowCapture').listeners.input({inputType:'insertFromPaste'});
 await ready(app);
 assert.equal(app.posted('/api/translate').length,1);
 assert.equal(calls.length,2);assert.equal(calls[0].english,'Improve the login page.');
 assert.equal(calls[0].captureId,'one-recording');assert.equal(calls[1],'closed');
 // Each stage reports its own time; no recording ran, so there is no after-Stop total.
 assert.match(n.get('stageStatus').textContent,/^Paste requested · prompt \d+\.\d s · paste request \d+\.\d s$/);
 vm.runInContext('update();update();',app.context);await ready(app);
 assert.equal(calls.length,2,'Repeated renders must never paste again');
 assert.equal(app.posted('/api/send').length,0);assert.equal(app.posted('/api/catalog').length,0);
 app=boot({quick:true,provider:'wispr',status:{nativeDesktop:true},translate:{english:'Keep React.'}});
 calls=native(app,{failure:true});await ready(app);n=app.nodes;
 await app.context.window.relayQuickToggle();
 n.get('flowCapture').value='خلي React';n.get('flowCapture').listeners.input({inputType:'insertFromPaste'});
 await ready(app);assert.equal(calls.length,1);assert.equal(n.get('english').value,'Keep React.');
 assert.match(n.get('notice').textContent,/focus changed/);
 assert.equal(n.get('stageStatus').textContent,'Paste not requested');
 assert.equal(app.context.document.documentElement.dataset.captureStage,'review');
 vm.runInContext('update();',app.context);await ready(app);assert.equal(calls.length,1);
 app=boot({quick:true,provider:'wispr',status:{nativeDesktop:true},translate:{english:'Review the page.'}});
 calls=native(app,{available:false});await ready(app);n=app.nodes;
 await app.context.window.relayQuickToggle();
 n.get('flowCapture').value='راجع الصفحة';n.get('flowCapture').listeners.input({inputType:'insertFromPaste'});
 await ready(app);assert.equal(calls.length,0);assert.equal(n.get('english').value,'Review the page.');
 app=boot({status:{nativeDesktop:true}});calls=native(app);await ready(app);
 app.nodes.get('english').value='Manually edited draft';app.nodes.get('english').listeners.input();
 await ready(app);assert.equal(calls.length,0);
 console.log('Automatic paste: exact prompt, one attempt, no session routes, safe recovery and main-draft isolation passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
