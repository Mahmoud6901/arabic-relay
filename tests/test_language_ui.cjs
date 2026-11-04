// The English option: nothing the app can show stays Arabic in English, and nothing changes in Arabic.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {boot,flush}=require('./test_translation_ui.cjs');
const root=path.join(__dirname,'..','src','arabic_relay');
const html=fs.readFileSync(path.join(root,'index.html'),'utf8').replace(/\r\n/g,'\n');
const ARABIC=/[؀-ۿ]/;
// Examples of Arabic that stay Arabic on purpose: the language's own name, a sample of a heard Arabic word.
const STAYS_ARABIC=new Set(['العربية','ريدزاين']);

function language(stored,storageUnavailable=false){
 const documentElement={dataset:{}},context=vm.createContext({console,document:{documentElement},window:{location:{search:''}},localStorage:{getItem:key=>{if(storageUnavailable)throw new Error('Storage unavailable');return key==='relay-language'?stored:null;}},Object,RegExp,String});
 for(const [,script] of html.slice(0,html.indexOf('<style>')).matchAll(/<script>([\s\S]*?)<\/script>/g))vm.runInContext(script,context);
 return {context,documentElement,tr:(text,values)=>{context.__text=text;context.__values=values;return vm.runInContext('tr(__text,__values)',context);}};
}
const english=language('en'),arabic=language('ar');
const missing=[];
function expectEnglish(where,text){const shown=english.tr(text);if(ARABIC.test(shown))missing.push(`${where}: ${text}`);}

// 1. A saved Arabic choice stays exactly as written; otherwise English is the default.
assert.equal(arabic.documentElement.lang,undefined);assert.equal(arabic.documentElement.dir,undefined);
assert.equal(arabic.tr('جاهز'),'جاهز');
assert.equal(arabic.tr('{count} من 10',{count:3}),'3 من 10');
for(const stored of [null,'','fr']){
 assert.equal(language(stored).documentElement.lang,'en');
 assert.equal(language(stored).documentElement.dir,'ltr');
}
assert.equal(language(null,true).documentElement.lang,'en','Unavailable storage also defaults to English');

// 2. English sets the direction before the first paint and fills in values.
assert.equal(english.documentElement.lang,'en');assert.equal(english.documentElement.dir,'ltr');
assert.equal(english.tr('جاهز'),'Ready');
assert.equal(english.tr('{count} من 10',{count:3}),'3 of 10');
assert.equal(english.tr('Notepad'),'Notepad','App names and anything without an entry pass through');
assert.equal(english.tr('constructor'),'constructor','The table is only ever asked about its own entries');
assert.equal(english.tr(undefined),undefined);
assert.equal(english.tr('Ctrl + Alt + R مش متاح، وممكن تطبيق تاني بيستخدمه. اختار اختصار تاني من الإعدادات.'),'Ctrl + Alt + R is not available, and another app may be using it. Choose another shortcut in Settings.');

// 3. Every visible piece of the static page has English. The hidden engine block is never shown.
const engineStart=html.indexOf('<div id="engine"'),engineEnd=html.indexOf('<dialog id="confirmDialog"');
assert.ok(engineStart>0&&engineEnd>engineStart);
const bodyStart=html.indexOf('<body'),scriptStart=html.indexOf('<script>\nlocalizeNode');
assert.ok(bodyStart>0&&scriptStart>engineEnd,'Find the page and main script boundaries before checking translations');
const markup=(html.slice(bodyStart,engineStart)+html.slice(engineEnd,scriptStart)).replace(/<!--[\s\S]*?-->/g,'');
for(const [,text] of markup.matchAll(/>([^<>]*[؀-ۿ][^<>]*)</g))if(!STAYS_ARABIC.has(text.trim()))expectEnglish('page',text.trim());
for(const [,,value] of markup.matchAll(/\b(aria-label|title|placeholder)="([^"]*[؀-ۿ][^"]*)"/g))if(!STAYS_ARABIC.has(value))expectEnglish('label',value);

// 4. In the script, Arabic only ever appears inside tr(). Every tr() has English.
const script=html.slice(html.indexOf('<script>\nlocalizeNode'),html.lastIndexOf('</script>'));
const calls=[...script.matchAll(/\btr\(\s*(?:'((?:[^'\\\n]|\\.)*)'|"((?:[^"\\\n]|\\.)*)")/g)].map(match=>match[1]??match[2]);
for(const text of calls)if(ARABIC.test(text))expectEnglish('script',text);
const outside=script.replace(/\btr\(\s*(?:'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*")/g,'tr(');
for(const line of outside.split('\n'))if(ARABIC.test(line))missing.push(`script, outside tr(): ${line.trim().slice(0,160)}`);

// 5. Every message the Python side can send has English. Values inside a message become a sample.
const FORMAT_HELP='Arabic Relay محتاج نسخة faster-whisper (Systran/faster-whisper-large-v3).';
const NOT_MESSAGES=/^(ARTICLES|LETTERS|FOLD|ARABIC_FOLD|ARABIC_PUNCTUATION|EXAMPLES)\b/;
const IS_MESSAGE=/raise ValueError\(|^[A-Z_]+ = |self\.error = |\breturn\b|say\(error=|^\s*"[^"]*[؀-ۿ]/;
for(const file of fs.readdirSync(root).filter(name=>name.endsWith('.py'))){
 for(const line of fs.readFileSync(path.join(root,file),'utf8').split(/\r?\n/)){
  if(!ARABIC.test(line)||!IS_MESSAGE.test(line)||NOT_MESSAGES.test(line.trim()))continue;
  if(line.trim().startsWith('#'))continue;
  for(const [,single,double] of line.matchAll(/f?'((?:[^'\\]|\\.)*)'|f?"((?:[^"\\]|\\.)*)"/g)){
   let message=single??double;
   if(!ARABIC.test(message)||/\[\d+:/.test(message))continue;     // the translator's own worked examples
   message=message.replace(/\{[^}]*\}/g,'123');
   if(line.includes('+ FORMAT_HELP'))message+=FORMAT_HELP;
   expectEnglish(file,message);
  }
 }
}
assert.deepEqual(missing,[],`No English for:\n${missing.join('\n')}`);

// 6. The markup walker changes text and labels, keeps spacing, and leaves the engine alone.
const textNode=value=>({nodeType:3,nodeValue:value});
const labelled={nodeType:1,attributes:{'aria-label':'إلغاء',title:'إلغاء'},getAttribute(name){return this.attributes[name]??null;},setAttribute(name,value){this.attributes[name]=value;},childNodes:[textNode('  مسح السجل ')]};
const engine={nodeType:1,id:'engine',childNodes:[textNode('جاهز')]};
english.context.__tree={nodeType:1,childNodes:[labelled,engine,textNode('Keep me')]};
vm.runInContext('localizeNode(__tree)',english.context);
assert.equal(labelled.attributes['aria-label'],'Cancel');assert.equal(labelled.attributes.title,'Cancel');
assert.equal(labelled.childNodes[0].nodeValue,'  Clear history ');
assert.equal(engine.childNodes[0].nodeValue,'جاهز');
const arabicNode=textNode('مسح السجل');arabic.context.__tree=arabicNode;
vm.runInContext('localizeNode(__tree)',arabic.context);assert.equal(arabicNode.nodeValue,'مسح السجل');

// 7. The real interface script in English, and the control in Settings.
(async()=>{
 let app=boot({language:null,status:{nativeDesktop:true}});await flush();let n=app.nodes;
 assert.equal(app.storage.has('relay-language'),false,'This is a first launch without a saved language');
 assert.equal(app.context.document.documentElement.dir,'ltr');
 assert.equal(n.get('languageEnglish').getAttribute('aria-pressed'),'true');assert.equal(n.get('languageArabic').getAttribute('aria-pressed'),'false');
 assert.ok(!ARABIC.test(n.get('privacySummary').textContent),n.get('privacySummary').textContent);
 assert.ok(!ARABIC.test(n.get('modeHelp').textContent),n.get('modeHelp').textContent);
 // A message from Python reaches the screen in English.
 vm.runInContext("notice('فيه تحميل شغال بالفعل.')",app.context);assert.equal(n.get('notice').textContent,'A download is already running.');
 // History: English dates and buttons, and the kept-text note. The user's own words are never translated.
 vm.runInContext("historyItems=[{id:'a',sentAt:1762000000,english:'جاهز',destinations:['اتنسخ، ما اتلصقش']}];renderHistory();",app.context);
 const row=n.get('historyList').innerHTML;
 assert.ok(row.includes('Copied, not pasted'),row);assert.ok(row.includes('>Copy<')&&row.includes('>Delete<'),row);
 assert.ok(row.includes('<div class="history-prompt" dir="auto">جاهز</div>'),'The user\'s own text stays as it is');
 assert.ok(!ARABIC.test(row.replace('جاهز','')),row);
 // Arrow keys follow the reading direction.
 vm.runInContext("selectTab('history')",app.context);
 let prevented=false;n.get('tabs').listeners.keydown({key:'ArrowRight',preventDefault(){prevented=true;}});
 assert.ok(prevented);assert.equal(n.get('modelsButton').getAttribute('aria-selected'),'true','In English, right moves forward');
 // Switching back stores the choice, reloads and returns to Settings.
 let reloads=0;app.context.window.location.reload=()=>{reloads++;};
 n.get('languageEnglish').listeners.click();assert.equal(reloads,0,'Choosing the current language does nothing');
 n.get('languageArabic').listeners.click();assert.equal(reloads,1);
 assert.equal(app.storage.get('relay-language'),'ar');assert.equal(app.storage.get('relay-reopen-tab'),'settings');

 // A saved Arabic preference keeps the screen right to left and its text untouched.
 app=boot({language:'ar',status:{nativeDesktop:true}});await flush();n=app.nodes;
 assert.equal(app.context.document.documentElement.dir,undefined);
 assert.equal(n.get('languageArabic').getAttribute('aria-pressed'),'true');
 vm.runInContext("notice('فيه تحميل شغال بالفعل.')",app.context);assert.equal(n.get('notice').textContent,'فيه تحميل شغال بالفعل.');
 reloads=0;app.context.window.location.reload=()=>{reloads++;};
 n.get('languageEnglish').listeners.click();assert.equal(reloads,1);assert.equal(app.storage.get('relay-language'),'en');
 vm.runInContext("selectTab('history')",app.context);
 n.get('tabs').listeners.keydown({key:'ArrowLeft',preventDefault(){}});
 assert.equal(n.get('modelsButton').getAttribute('aria-selected'),'true','In Arabic, left moves forward');

 // Back in Settings after the reload.
 app=boot({status:{nativeDesktop:true},reopenTab:'settings'});await flush();n=app.nodes;
 assert.equal(n.get('settingsButton').getAttribute('aria-selected'),'true');assert.equal(app.storage.get('relay-reopen-tab'),undefined);

 // A newly opened pill uses the same saved language and translates dynamic failures.
 app=boot({language:null,quick:true,reopenTab:'settings',status:{nativeDesktop:true}});await flush();n=app.nodes;
 assert.equal(app.context.document.documentElement.dir,'ltr');
 assert.equal(app.storage.get('relay-reopen-tab'),'settings','The pill must not consume the main window tab preference');
 vm.runInContext("notice('محرك الكلام وقف. سجّل تاني عشان يشتغل.')",app.context);
 assert.equal(n.get('captureLabel').textContent,'The speech engine stopped. Record again to restart it.');

 // A server notice stays translated when the page appends its own explanation.
 app=boot({language:'en',translate:{english:'Keep these words.',notice:'اتلغى التجهيز.'}});await flush();n=app.nodes;
 n.get('arabic').value='كلام المستخدم';await n.get('translate').click();
 assert.equal(n.get('english').value,'Keep these words.');
 assert.equal(n.get('notice').textContent,'Preparing cancelled. The English draft is ready to review.');

 console.log('Language: English default, saved Arabic preference, every string, direction, dates, keys, and the Settings control verified.');
})().catch(e=>{console.error(e);process.exitCode=1;});
