// The themed confirmation that replaced native confirm(), and the pill's close, which never asks anything.
const assert=require('node:assert/strict');
const vm=require('node:vm');
const {boot,flush}=require('./test_translation_ui.cjs');
(async()=>{
 const app=boot({provider:'whisper'});await flush();
 const n=app.nodes;
 app.context.confirm=()=>{throw Error('Native confirmation must not open');};
 const ask=()=>vm.runInContext("askConfirm('تمسح النص ده؟','هيتمسح من السجل.','مسح')",app.context);
 // It says what it will do, and the safe answer has the focus.
 const opener=n.get('clearHistory');app.context.document.activeElement=opener;
 let answer=ask();
 assert.equal(n.get('confirmDialog').open,true);
 assert.equal(n.get('confirmTitle').textContent,'تمسح النص ده؟');
 assert.equal(n.get('confirmDescription').textContent,'هيتمسح من السجل.');
 assert.equal(n.get('confirmAccept').textContent,'مسح');
 assert.equal(n.get('confirmCancel').focused,true);
 // A second request while one is open is refused, and the open one still waits for its answer.
 assert.equal(await ask(),false);
 assert.equal(n.get('confirmDialog').open,true);
 n.get('confirmCancel').click();
 assert.equal(await answer,false);
 assert.equal(n.get('confirmDialog').open,false);
 assert.equal(opener.focused,true,'cancelling returns the focus');
 // Escape and any other close count as no.
 answer=ask();n.get('confirmDialog').listeners.cancel({preventDefault(){}});
 assert.equal(await answer,false);assert.equal(n.get('confirmDialog').open,false);
 answer=ask();n.get('confirmDialog').close();
 assert.equal(await answer,false);
 answer=ask();n.get('confirmAccept').click();
 assert.equal(await answer,true);assert.equal(n.get('confirmDialog').open,false);
 // The pill has nothing to review: closing it asks nothing and sends nothing, unless a request is still running.
 const pill=boot({quick:true,provider:'wispr'});await flush();
 pill.context.confirm=app.context.confirm;
 pill.nodes.get('english').value='Some prepared text';
 vm.runInContext('busy=true',pill.context);
 assert.equal(pill.context.window.relayQuickClose(),false);
 vm.runInContext('busy=false',pill.context);
 assert.equal(pill.context.window.relayQuickClose(),true);
 assert.notEqual(pill.nodes.get('confirmDialog').open,true);
 assert.equal(pill.posted('/api/send').length,0);
 console.log('Themed confirmation: cancel, Escape, close, accept, focus and the pill closing without a question passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
