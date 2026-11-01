// Exercise Flow's text-insertion handoff without a microphone or a real send.
const assert=require('node:assert/strict');
const {boot,flush}=require('./test_translation_ui.cjs');
(async()=>{
 const app=boot({quick:true,provider:'whisper',translate:{english:'Improve the login page.'}});
 await flush();
 // The main window can change providers after the popup was opened.
 app.context.localStorage.setItem('relay-dictation-provider','wispr');
 await app.context.window.relayQuickToggle();
 assert.match(app.nodes.get('notice').textContent,/Wispr Flow shortcut/,'popup kept the old provider');
 const input=app.nodes.get('flowCapture');
 assert.ok(input&&!input.hidden&&input.focused,'Flow needs a visible, focused capture input');
 input.value='حسن صفحة الدخول';input.listeners.input();
 await new Promise(r=>setTimeout(r,1250));await flush();
 assert.equal(app.nodes.get('english').value,'Improve the login page.');
 assert.equal(app.context.document.documentElement.dataset.captureStage,'review');
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
 assert.equal(inline.context.document.documentElement.dataset.captureStage,'review');
 assert.equal(inline.posted('/api/send').length,0);
 console.log('Flow capture: current provider, focused input, automatic preparation and review passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
