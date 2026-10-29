const $ = id=>document.getElementById(id);
async function status() { const data=await chrome.storage.session.get('status');$('status').textContent=data.status||'Keep the Arabic Relay desktop app running.'; }
$('connect').onclick=async()=>{
  $('connect').disabled=true;
  try {
    const result=await chrome.runtime.sendMessage({type:'pair',code:$('code').value.trim()});
    if(result.error)throw Error(result.error);
    $('code').value='';await status();
  } catch(error){$('status').textContent=error.message;}
  finally{$('connect').disabled=false;}
};
$('open').onclick=async()=>{await chrome.tabs.create({url:'https://chatgpt.com/'});await chrome.tabs.create({url:'https://claude.ai/new'});};
$('refresh').onclick=async()=>{await chrome.runtime.sendMessage({type:'refresh'});await status();};
status();
