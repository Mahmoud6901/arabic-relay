// Real popup code, fake microphone/network: never send to a real session.
const assert=require('node:assert/strict');
const vm=require('node:vm');
const {boot,flush}=require('./test_translation_ui.cjs');
(async()=>{
 const app=boot({quick:true,provider:'wispr',status:{nativeDesktop:true},translate:{english:'Improve the login page.'}});
 await flush();const n=app.nodes;
 await app.context.window.relayQuickToggle();
 assert.equal(n.get('arabic').focused,true);
 assert.match(n.get('notice').textContent,/Wispr Flow shortcut/);
 assert.equal(app.context.document.documentElement.dataset.source,'true');
 n.get('arabic').value='حسن صفحة الدخول';n.get('arabic').listeners.input();
 assert.equal(n.get('send').disabled,true);
 await new Promise(r=>setTimeout(r,1250));await flush();
 assert.equal(app.posted('/api/translate').length,1);
 assert.equal(n.get('english').value,'Improve the login page.');
 assert.equal(app.posted('/api/send').length,0);
 assert.equal(app.posted('/api/restore-english').length,0);
 // Review and choose recipients explicitly. No recipients from the main editor.
 assert.equal(n.get('send').disabled,true);
 vm.runInContext("selected.set('test',{id:'test',projectId:'desktop:direct:codex:test',title:'Test',project:'Test'});update();",app.context);
 assert.equal(n.get('send').disabled,false);
 n.get('english').value='The exact reviewed prompt';n.get('english').listeners.input();
 await n.get('send').click();await flush();
 assert.equal(app.posted('/api/send').length,1);
 assert.equal(app.posted('/api/send')[0].body.english,'The exact reviewed prompt');
 // Cancelling close preserves the popup draft. Busy operations cannot be dismissed.
 app.context.confirm=()=>false;
 n.get('english').listeners.input();
 assert.equal(app.context.window.relayQuickClose(),false);
 vm.runInContext('busy=true;',app.context);
 assert.equal(app.context.window.relayQuickClose(),false);
 vm.runInContext('busy=false;',app.context);
 // Clear stops the queued Flow preparation.
 n.get('arabic').value='new text';n.get('arabic').listeners.input();n.get('clearDraft').click();
 await new Promise(r=>setTimeout(r,1250));
 assert.equal(app.posted('/api/translate').length,1);
 // The regular workspace does not automatically craft Flow input.
 const main=boot({provider:'wispr'});await flush();
 main.nodes.get('arabic').value='نص';main.nodes.get('arabic').listeners.input();
 await new Promise(r=>setTimeout(r,1250));
 assert.equal(main.posted('/api/translate').length,0);
 console.log('Quick capture: Flow focus, automatic prompt, explicit recipients/send, close protection and draft isolation passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
