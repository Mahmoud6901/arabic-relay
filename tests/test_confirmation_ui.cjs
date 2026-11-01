// Test the synchronous native-close handshake with the asynchronous themed dialog.
const assert=require('node:assert/strict');
const {boot,flush}=require('./test_translation_ui.cjs');
(async()=>{
 const app=boot({quick:true,provider:'wispr'});await flush();
 const n=app.nodes;let closes=0,allowed=false;
 app.context.confirm=()=>{throw Error('Native confirmation must not open');};
 app.context.window.pywebview.api.close_quick=async()=>{closes++;allowed=app.context.window.relayQuickClose();};
 n.get('english').value='Keep this exact draft';
 app.context.document.activeElement=n.get('english');
 assert.equal(app.context.window.relayQuickClose(),false);
 assert.equal(n.get('draftConfirm').open,true);
 assert.equal(n.get('draftConfirmCancel').focused,true);
 assert.equal(app.context.document.documentElement.dataset.captureStage,'review');
 assert.equal(app.context.window.relayQuickClose(),false); // repeated window-close request
 n.get('draftConfirmCancel').click();await flush();
 assert.equal(closes,0);assert.equal(n.get('english').value,'Keep this exact draft');
 assert.equal(n.get('english').focused,true);
 app.context.window.relayQuickClose();
 n.get('draftConfirm').listeners.cancel({preventDefault(){}});await flush();
 assert.equal(closes,0);assert.equal(n.get('draftConfirm').open,false);
 app.context.window.relayQuickClose();n.get('draftConfirmAccept').click();await flush();
 assert.equal(closes,1);assert.equal(allowed,true);
 assert.equal(app.posted('/api/send').length,0);
 // Approval cannot bypass a later draft's confirmation.
 assert.equal(app.context.window.relayQuickClose(),false);
 assert.equal(n.get('draftConfirm').open,true);n.get('draftConfirmCancel').click();await flush();
 n.get('english').value='';assert.equal(app.context.window.relayQuickClose(),true);
 // Replacing a draft uses the same themed choice and preserves it on cancellation.
 app.context.localStorage.setItem('relay-dictation-provider','whisper');
 n.get('english').value='Existing draft';
 const replacement=app.context.window.relayQuickToggle();
 assert.equal(n.get('draftConfirmTitle').textContent,'Start a new recording?');
 n.get('draftConfirmCancel').click();await replacement;
 assert.equal(n.get('english').value,'Existing draft');
 console.log('Themed confirmation: cancel, Escape, discard, repeated close, focus and replacement passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
