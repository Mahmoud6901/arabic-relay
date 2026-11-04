// Real capture preparation and automatic native delivery, with fake OS and network calls.
const assert=require('node:assert/strict');
const vm=require('node:vm');
const {boot,flush}=require('./test_translation_ui.cjs');
async function ready(app){await flush();await flush();await flush();}
function native(app,{failure=false,available=true}={}){
 const calls=[];calls.stages=[];calls.kept=[];
 Object.assign(app.context.window.pywebview.api,{
  keep_text:async(body)=>{calls.kept.push(body.text);return {saved:true,copied:true};},
  quick_stage:async(stage)=>{calls.stages.push(stage);},
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
 assert.ok(!calls.stages.includes('message'),'Successful dictation only ever shows the pill');
 assert.equal(calls.kept.length,0);
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
 assert.equal(n.get('stageStatus').textContent,'Paste not requested');
 // A paste that cannot land keeps the words and says where they went, in the pill.
 assert.deepEqual(calls.kept,['Keep React.']);
 assert.equal(n.get('captureLabel').textContent,'ما قدرتش ألصق. اتنسخ وهتلاقيه في السجل.');
 assert.equal(app.context.document.documentElement.dataset.captureStage,'message');
 vm.runInContext('update();',app.context);await ready(app);assert.equal(calls.length,1);assert.equal(calls.kept.length,1);
 app=boot({quick:true,provider:'wispr',status:{nativeDesktop:true},translate:{english:'Review the page.'}});
 calls=native(app,{available:false});await ready(app);n=app.nodes;
 await app.context.window.relayQuickToggle();
 n.get('flowCapture').value='راجع الصفحة';n.get('flowCapture').listeners.input({inputType:'insertFromPaste'});
 await ready(app);assert.equal(calls.length,0);assert.equal(n.get('english').value,'Review the page.');
 // No app to paste into, as when the shortcut was pressed in Relay's own window.
 assert.deepEqual(calls.kept,['Review the page.']);
 assert.equal(n.get('captureLabel').textContent,'مفيش تطبيق ألصق فيه. اتنسخ وهتلاقيه في السجل.');
 // A likely mishearing neither opens review nor delays closing after successful paste.
 app=boot({quick:true,storedMode:'inline',status:{nativeDesktop:true},inlineHandler:async(path,body)=>path==='/api/inline'?{status:'ready',requestId:body.requestId,revision:body.revision,workingText:body.text,outputText:body.text,issues:[],replacements:[]}:{status:'ready',proof:{test:true}}});
 calls=native(app);await ready(app);n=app.nodes;
 vm.runInContext("captureId='one-recording';pasteArmed=true;pasteInputReceived=true;",app.context);
 app.context.acceptInlineRecognition({text:'Why do I want it',doubtfulWords:['Why do I'],issues:[]});
 vm.runInContext('inlineAutoPrepare=false;prepareInline();',app.context);await ready(app);
 assert.equal(calls.length,2,'Paste and close do not wait for the warning');
 assert.match(n.get('notice').textContent,/Check “Why do I”: it may have been misheard\./);
 assert.doesNotMatch(n.get('notice').className,/success/,'The shortcut window hides success notices');
 assert.equal(calls[1],'closed');
 assert.ok(!calls.stages.includes('message'));
 // Romanized Arabic and ASR warnings do not require acknowledgement in shortcut dictation.
 app=boot({quick:true,storedMode:'inline',status:{nativeDesktop:true},inlineHandler:async(path,body)=>path==='/api/inline'?{status:'ready',requestId:body.requestId,revision:body.revision,workingText:body.text,outputText:'Review the code.',spans:[{romanized:true}],issues:[],replacements:[]}:{status:'ready',proof:{test:true}}});
 calls=native(app);await ready(app);
 vm.runInContext("captureId='one-recording';pasteArmed=true;pasteInputReceived=true;",app.context);
 app.context.acceptInlineRecognition({text:"Raja'un the code.",issues:[{code:'asr_warning'}]});
 vm.runInContext('inlineAutoPrepare=false;prepareInline();',app.context);await ready(app);
 assert.equal(app.nodes.get('inlineAcknowledge').checked,false);
 assert.equal(calls[0].english,'Review the code.');assert.equal(calls[1],'closed');
 assert.ok(!calls.stages.includes('message'));
 // Incomplete translation is retained for recovery rather than silently pasted or discarded.
 app=boot({quick:true,storedMode:'inline',status:{nativeDesktop:true},inlineHandler:async(path,body)=>({status:'needs_review',requestId:body.requestId,revision:body.revision,workingText:body.text,partialText:'It is جامد.',issues:[{message:'Ambiguous phrase'}],replacements:[]})});
 calls=native(app);await ready(app);
 vm.runInContext("captureId='one-recording';pasteArmed=true;pasteAvailable=true;pasteInputReceived=true;",app.context);
 app.context.acceptInlineRecognition({text:'It is جامد.',issues:[]});
 vm.runInContext('inlineAutoPrepare=false;prepareInline();',app.context);await ready(app);
 assert.equal(calls.length,0);assert.equal(app.nodes.get('english').value,'It is جامد.');
 assert.equal(calls.kept.length,1);
 assert.ok(app.nodes.get('captureLabel').textContent.startsWith('الترجمة ما كملتش.'),app.nodes.get('captureLabel').textContent);
 assert.equal(app.context.document.documentElement.dataset.captureStage,'message');
 app=boot({status:{nativeDesktop:true}});calls=native(app);await ready(app);
 app.nodes.get('english').value='Manually edited draft';app.nodes.get('english').listeners.input();
 await ready(app);assert.equal(calls.length,0);
 // Keeping the words can itself fail: the pill says only what actually happened.
 app=boot({quick:true,provider:'wispr',status:{nativeDesktop:true},translate:{english:'Keep this.'}});
 calls=native(app,{failure:true});await ready(app);n=app.nodes;
 app.context.window.pywebview.api.keep_text=async()=>({saved:true,copied:false});
 await app.context.window.relayQuickToggle();
 n.get('flowCapture').value='خليها';n.get('flowCapture').listeners.input({inputType:'insertFromPaste'});
 await ready(app);
 assert.equal(n.get('captureLabel').textContent,'ما قدرتش ألصق. هتلاقيه في السجل.');
 console.log('Automatic paste: exact prompt, one attempt, no session routes, kept words and main-draft isolation passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
