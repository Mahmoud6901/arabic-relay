importScripts('shared.js');
const SITE_URLS = ['https://chatgpt.com/*','https://claude.ai/*'];
const BASE = 'http://127.0.0.1:8791';
let syncing = false;
chrome.storage.local.setAccessLevel({accessLevel:'TRUSTED_CONTEXTS'});
chrome.alarms.create('relay-sync',{periodInMinutes:0.5});
async function request(path,body,token) {
  const response = await fetch(BASE+path,{method:'POST',headers:{'Content-Type':'application/json',...(token?{'Authorization':'Bearer '+token}:{})},body:JSON.stringify(body),signal:AbortSignal.timeout(8000)});
  const result = await response.json();
  if (!response.ok) throw Error(result.error || 'Relay is unavailable.');
  return result;
}
async function run(command, token) {
  const {seen={}} = await chrome.storage.session.get('seen');
  if (seen[command.id]) return; // Survives worker suspension; never replay a leased command.
  seen[command.id] = Date.now();
  for (const key of Object.keys(seen)) if (seen[key] < Date.now()-3600000) delete seen[key];
  await chrome.storage.session.set({seen});
  let result;
  try {
    if (!RelayAddress.parse(command.url) || command.expires*1000 <= Date.now()) throw Error('The send request expired.');
    const tab = await chrome.tabs.create({url:command.url,active:false});
    let ready = false;
    while (Date.now() < command.expires*1000-4000) {
      const current = await chrome.tabs.get(tab.id);
      if (current.status === 'complete' && current.url === command.url) {
        try { await chrome.tabs.sendMessage(tab.id,{type:'discover'});ready=true;break; } catch {}
      }
      await new Promise(resolve=>setTimeout(resolve,400));
    }
    if (!ready) throw Error('The selected conversation did not open. Check the browser for sign-in or an offline session.');
    // sendMessage is issued once only. An unknown response must never cause a second Send.
    result = await chrome.tabs.sendMessage(tab.id,{type:'send',command});
  } catch (error) { result = {error:error.message}; }
  try { await request('/bridge/result',{id:command.id,...result},token); }
  catch { /* Relay will report uncertainty; never retry the submission. */ }
}
async function sync() {
  if (syncing) return;
  syncing = true;
  try {
    const {token} = await chrome.storage.local.get('token');
    if (!token) return;
    const tabs = await chrome.tabs.query({url:SITE_URLS});
    const snapshots = [];
    for (const tab of tabs) {
      try {
        const data = await chrome.tabs.sendMessage(tab.id,{type:'discover'});
        if (Array.isArray(data?.chats)) snapshots.push({tabId:tab.id,chats:data.chats});
      } catch { /* A closed, loading or pre-installation tab has no usable content script. */ }
    }
    const result = await request('/bridge/sync',{tabs:snapshots},token);
    await chrome.storage.session.set({status:`Connected · ${snapshots.reduce((n,t)=>n+t.chats.length,0)} discovered links`,lastSync:Date.now()});
    for (const command of result.commands) await run(command,token);
  } catch (error) { await chrome.storage.session.set({status:error.message}); }
  finally { syncing = false; }
}
chrome.alarms.onAlarm.addListener(()=>sync());
chrome.runtime.onMessage.addListener((message,sender,respond)=>{
  const options = sender.url === chrome.runtime.getURL('options.html');
  if (message.type === 'pair' && options) {
    request('/bridge/pair',{code:message.code}).then(async result=>{
      await chrome.storage.local.set({token:result.token});
      await sync();respond({connected:true});
    }).catch(error=>respond({error:error.message}));
    return true;
  }
  if (message.type === 'refresh' && options) {sync().then(()=>respond({done:true}));return true;}
  if (message.type === 'snapshot' && sender.tab && /^https:\/\/(chatgpt\.com|claude\.ai)\//.test(sender.url||'')) {sync();}
});
