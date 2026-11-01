// A slow/unavailable destination service must not freeze local dictation or draft editing.
const assert=require('node:assert/strict');
const vm=require('node:vm');
const {boot,flush}=require('./test_translation_ui.cjs');
(async()=>{
 let release;
 const catalog=new Promise(resolve=>release=resolve);
 const app=boot({quick:true,provider:'whisper',status:{nativeDesktop:true},catalogHandler:()=>catalog});
 let ready=0;app.context.window.pywebview.api.quick_ready=async()=>{ready++;};
 await flush();
 try {
  assert.equal(app.nodes.get('english').disabled,false,'session discovery blocks prompt editing');
  assert.equal(app.nodes.get('record').disabled,false,'session discovery blocks recording');
  assert.ok(ready>0,'native shortcut waits for destination discovery');
  app.nodes.get('english').value='Draft survives a late catalog';
  vm.runInContext('busy=true;update();',app.context);
 } finally { release({projects:[],live:false}); }
 await flush();
 assert.equal(vm.runInContext('busy',app.context),true,'catalog completion cleared an active operation');
 assert.equal(app.nodes.get('english').value,'Draft survives a late catalog');
 vm.runInContext('busy=false;update();',app.context);
 assert.equal(app.nodes.get('english').disabled,false);
 console.log('Slow session discovery leaves editing and recording responsive.');
})().catch(e=>{console.error(e);process.exitCode=1;});
