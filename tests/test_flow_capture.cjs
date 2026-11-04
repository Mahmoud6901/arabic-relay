// Exercise Flow's text-insertion handoff without a microphone or a real send. The pill says what it does; no review window.
const assert=require('node:assert/strict');
const {boot,flush}=require('./test_translation_ui.cjs');
(async()=>{
 const app=boot({quick:true,provider:'whisper',translate:{english:'Improve the login page.'}});
 await flush();
 let keyboard=0;app.context.window.pywebview.api.quick_keyboard=async()=>{keyboard++;};
 // The main window can change providers after the popup was opened.
 app.context.localStorage.setItem('relay-dictation-provider','wispr');
 await app.context.window.relayQuickToggle();
 assert.equal(app.context.document.documentElement.dataset.pill,'flow','popup kept the old provider');
 assert.equal(app.nodes.get('captureLabel').textContent,'اتكلم بـ Flow');
 const input=app.nodes.get('flowCapture');
 assert.ok(input&&!input.hidden&&input.focused,'Flow needs a visible, focused capture input');
 assert.equal(keyboard,1,'Flow types into the pill, so the pill asks for the keyboard');
 input.value='حسن صفحة الدخول';input.listeners.input();
 await new Promise(r=>setTimeout(r,1250));await flush();
 assert.equal(app.nodes.get('english').value,'Improve the login page.');
 assert.equal(app.context.document.documentElement.dataset.captureStage,'message');
 assert.equal(app.posted('/api/send').length,0);
 // Inline mode preserves the mixed source and waits for composition to finish.
 const inline=boot({quick:true,provider:'wispr',storedMode:'inline',inlineHandler:async(path,body)=>({
  status:'ready',requestId:body.requestId,revision:body.revision,workingText:body.text,
  outputText:'Improve MapView.',issues:[],replacements:[]
 })});
 await flush();await inline.context.window.relayQuickToggle();
 const field=inline.nodes.get('flowCapture');
 field.listeners.compositionstart();
 field.value='حسن MapView.';field.listeners.input();
 assert.equal(field.disabled,false,'composition must not disable the Flow input');
 await new Promise(r=>setTimeout(r,1250));
 assert.equal(inline.posted('/api/inline').length,0);
 field.listeners.compositionend();
 await new Promise(r=>setTimeout(r,1250));await flush();
 assert.equal(inline.posted('/api/inline')[0].body.text,'حسن MapView.');
 assert.equal(inline.nodes.get('english').value,'Improve MapView.');
 assert.equal(inline.context.document.documentElement.dataset.captureStage,'message');
 assert.equal(inline.posted('/api/send').length,0);
 // Flow's complete paste should not incur the pause used for incremental typing.
 const pasted=boot({quick:true,provider:'wispr',storedMode:'inline',inlineHandler:async(path,body)=>({
  status:'ready',requestId:body.requestId,revision:body.revision,workingText:body.text,
  outputText:'Review MapView.',issues:[],replacements:[]
 })});
 await flush();await pasted.context.window.relayQuickToggle();
 pasted.nodes.get('flowCapture').value='راجع MapView.';
 pasted.nodes.get('flowCapture').listeners.input({inputType:'insertFromPaste'});
 await flush();
 assert.equal(pasted.posted('/api/inline').length,1,'complete Flow paste waited for the typing timer');
 assert.equal(pasted.context.document.documentElement.dataset.captureStage,'message');
 assert.equal(pasted.posted('/api/send').length,0);
 // Being refused the keyboard still leaves the field ready.
 const refused=boot({quick:true,provider:'wispr'});await flush();
 refused.context.window.pywebview.api.quick_keyboard=async()=>{throw Error('No window');};
 await refused.context.window.relayQuickToggle();
 assert.equal(refused.nodes.get('flowCapture').focused,true);
 console.log('Flow capture: current provider, the keyboard, focused input, automatic preparation and message passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
