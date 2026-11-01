// Real interface code with mocked providers and destinations. Nothing leaves the process.
const assert=require('node:assert/strict');
const vm=require('node:vm');
const {boot,flush}=require('./test_translation_ui.cjs');
const edit=async(node,value)=>{node.value=value;await node.listeners.input?.();};
const mode=async(app,value)=>{app.nodes.get('promptMode').value=value;await app.nodes.get('promptMode').listeners.change();};
const ready=(body,text)=>({status:'ready',requestId:body.requestId,revision:body.revision,workingText:body.text,outputText:text,issues:[],replacements:[]});
(async()=>{
 let resolve,app=boot({inlineHandler:async(path,body)=>path==='/api/inline'?new Promise(r=>resolve=()=>r(ready(body,'Check the code.'))):{}});
 await flush();assert.equal(app.nodes.get('promptMode').value,'craft');
 await mode(app,'inline');const n=app.nodes;
 assert.equal(app.context.document.documentElement.dataset.mode,'inline');
 assert.equal(n.get('inlineReview').hidden,false);
 await edit(n.get('english'),'Check الكود.');
 const first=n.get('translate').click();await flush();
 assert.equal(n.get('inlineCancel').hidden,false);
 // Editing during translation cancels the job, and a late result must not overwrite the edit.
 await edit(n.get('english'),'My newer text');resolve();await first;
 assert.equal(n.get('english').value,'My newer text');
 assert.equal(app.posted('/api/inline/cancel').length,1);
 // Cancelling leaves the source intact and blocks Send until translated or explicitly edited.
 const second=n.get('translate').click();await flush();await n.get('inlineCancel').click();resolve();await second;
 assert.equal(n.get('english').value,'My newer text');
 assert.equal(n.get('send').disabled,true);
 // Switching modes also ignores a pending inline result.
 const third=n.get('translate').click();await flush();await mode(app,'craft');resolve();await third;
 assert.equal(n.get('english').value,'My newer text');
 assert.equal(n.get('inlineReview').hidden,true);
 // Persisted choice and the single-click send path. Validation is not a second approval button.
 app=boot({storedMode:'inline',inlineHandler:async(path,body)=>path==='/api/inline'?ready(body,'  Check MapView.\r\n'):{status:'ready',proof:{test:true}}});
 await flush();assert.equal(app.nodes.get('promptMode').value,'inline');
 await edit(app.nodes.get('english'),'  راجع MapView.\r\n');await app.nodes.get('translate').click();
 assert.equal(app.nodes.get('english').value,'  Check MapView.\r\n');
 vm.runInContext("selected.set('t',{id:'t',projectId:'desktop:test',project:'Test',title:'Test'});update();",app.context);
 await app.nodes.get('send').click();
 const sent=app.posted('/api/send');assert.equal(sent.length,1);
 assert.equal(sent[0].body.english,'  Check MapView.\r\n');assert.equal(sent[0].body.mode,'inline');
 await app.nodes.get('send').click();assert.equal(app.posted('/api/send').length,1);
 // A reviewed source edit needs translation again; a failed/partial model result never sends.
 app=boot({storedMode:'inline',inlineHandler:async()=>({status:'needs_review',issues:[{message:'Ambiguous phrase'}]})});
 await flush();await edit(app.nodes.get('inlineSource'),'It is جامد.');
 assert.equal(app.nodes.get('send').disabled,true);
 vm.runInContext("selected.set('t',{id:'t',projectId:'desktop:test'});update();",app.context);
 await app.nodes.get('send').click();assert.equal(app.posted('/api/send').length,0);
 // An ASR warning is acknowledged only for the current revision.
 await edit(app.nodes.get('english'),'It is solid.');
 vm.runInContext('inlineAsrWarning=true;update();',app.context);
 assert.equal(app.nodes.get('send').disabled,true);
 app.nodes.get('inlineAcknowledge').checked=true;await app.nodes.get('inlineAcknowledge').listeners.change();
 assert.equal(app.nodes.get('send').disabled,false);
 await edit(app.nodes.get('english'),'It is firm.');assert.equal(app.nodes.get('inlineAcknowledge').checked,false);
 // Changing protected terms reprocesses the mixed working source, not the already-translated output.
 app=boot({storedMode:'inline',inlineHandler:async(path,body)=>ready(body,'Check MapView.')});await flush();
 await edit(app.nodes.get('english'),'راجع MapView.');await app.nodes.get('translate').click();
 await edit(app.nodes.get('inlineTerms'),'MapView');await app.nodes.get('translate').click();
 assert.equal(app.posted('/api/inline').at(-1).body.text,'راجع MapView.');
 // Candidate acceptance is explicit and records recognition provenance before translation.
 vm.runInContext("inlineOriginalText='arrange the code';inlineSourceText='arrange the code';inlineCandidateText='أراجع الكود';",app.context);
 await app.nodes.get('inlineAcceptCandidate').click();await flush();
 assert.equal(app.posted('/api/inline').at(-1).body.text,'أراجع الكود');
 assert.ok(app.nodes.get('inlineProvenance').textContent.includes('arrange the code'));
 assert.equal(vm.runInContext('inlineOriginalText',app.context),'arrange the code');
 assert.equal(app.posted('/api/send').length,0);
 console.log('Inline mode: persistence, revision races, cancellation, one-click exact sending, source edits and recognition warnings passed.');
 process.exit(0);
})().catch(e=>{console.error(e);process.exit(1);});
