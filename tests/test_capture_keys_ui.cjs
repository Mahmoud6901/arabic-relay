// Enter finishes a shortcut recording; Escape cancels. Neither key submits to the target app.
const assert=require('node:assert/strict');
const vm=require('node:vm');
const {boot,flush}=require('./test_translation_ui.cjs');
(async()=>{
 const app=boot({quick:true,language:'en',status:{nativeDesktop:true}});await flush();
 let stops=0,closed=0;
 app.context.__stop=()=>stops++;
 app.context.window.pywebview.api.close_quick=async()=>{if(app.context.window.relayQuickClose())closed++;};
 const record=()=>vm.runInContext("listening=true;busy=false;quickEnding=false;recorder={state:'recording',stop(){this.state='inactive';__stop();}}",app.context);
 const key=(name,extra={})=>{let prevented=false;app.context.quickKeydown({key:name,preventDefault(){prevented=true;},...extra});return prevented;};
 record();assert.ok(key('Enter'));assert.equal(stops,1);
 assert.equal(vm.runInContext('discardAudio',app.context),false);
 assert.ok(key('Enter',{repeat:true}));assert.equal(stops,1);
 record();assert.ok(key('Escape'));assert.equal(closed,1);assert.equal(stops,2);
 assert.equal(vm.runInContext('discardAudio',app.context),true);
 for(const modifiers of [{shiftKey:true},{ctrlKey:true},{altKey:true},{metaKey:true},{isComposing:true}]){
  record();assert.equal(key('Enter',modifiers),false);assert.equal(stops,2);
 }
 vm.runInContext('listening=false;busy=true',app.context);app.context.window.relayQuickFinish();assert.equal(stops,2);
 assert.equal(app.posted('/api/send').length,0);
 const main=boot();await flush();let prevented=false;
 main.context.quickKeydown({key:'Enter',preventDefault(){prevented=true;}});assert.equal(prevented,false);

 // Flow can finish immediately instead of waiting for the typing pause, but respects IME composition.
 const flow=boot({quick:true,provider:'wispr',translate:{english:'Keep React.'}});await flush();
 await flow.context.window.relayQuickToggle();flow.nodes.get('flowCapture').value='خلي React';
 vm.runInContext('quickComposing=true',flow.context);flow.context.window.relayQuickFinish();assert.equal(flow.posted('/api/translate').length,0);
 vm.runInContext('quickComposing=false',flow.context);flow.context.window.relayQuickFinish();await flush();
 assert.equal(flow.posted('/api/translate').length,1);assert.equal(flow.nodes.get('english').value,'Keep React.');
 console.log('Enter/Esc: finish, cancel, repeat suppression, modifiers, IME, Flow and main-window isolation passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
