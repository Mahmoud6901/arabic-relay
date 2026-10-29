/* Reads rendered navigation links, never cookies, account tokens or private APIs. */
(() => {
  const provider = location.hostname === 'chatgpt.com' ? 'chatgpt' : 'claude';
  const normalize = text => String(text).replace(/\r\n/g,'\n').trim();
  const visible = e => !!e && !!e.getClientRects().length && !e.closest('[aria-hidden="true"],[inert]');
  function discover() {
    const selector = provider === 'chatgpt' ? 'a[data-interactive-row-link]' : 'a[data-row-main-button]';
    const chats = new Map();
    for (const a of document.querySelectorAll(selector)) {
      const address = RelayAddress.parse(a.href);
      if (!address || address.provider !== provider) continue;
      const title = (a.getAttribute('aria-label') || a.querySelector('[data-thread-title],[data-row-label]')?.textContent || a.textContent).trim();
      if (title) chats.set(address.id, {url:address.url,title:title.slice(0,240),offline:address.url.includes('/code/') && /\bOffline\b/i.test(a.textContent)});
    }
    const current = RelayAddress.parse(location.href);
    if (current && !chats.has(current.id)) {
      // The open conversation itself remains selectable when its sidebar is collapsed.
      chats.set(current.id,{url:current.url,title:document.title.replace(/\s*[-–]\s*(Claude(?: Code)?|ChatGPT)$/,'').slice(0,240)});
    }
    return [...chats.values()].slice(0,2000);
  }
  function composer() {
    const selector = provider === 'chatgpt' ? '[contenteditable="true"][role="textbox"][aria-label="Ask ChatGPT"]' :
      '[contenteditable="true"][data-testid="chat-input"],[contenteditable="true"][data-testid="code-prompt-input"]';
    const candidates = [...document.querySelectorAll(selector)].filter(visible);
    if (candidates.length !== 1) throw Error('The conversation has no unique, available prompt field. It may be offline or need sign-in.');
    return candidates[0];
  }
  function sendButton() {
    const labels = provider === 'chatgpt' ? ['Send'] : ['Send message','Send prompt'];
    const buttons = [...document.querySelectorAll('button')].filter(e=>visible(e) && labels.includes(e.getAttribute('aria-label')));
    if (buttons.length !== 1 || buttons[0].disabled || buttons[0].getAttribute('aria-disabled') === 'true') throw Error('Send is unavailable. Review the browser draft; nothing was submitted.');
    return buttons[0];
  }
  function checkTarget(command) {
    if (!RelayAddress.parse(command.url) || location.href !== command.url || Date.now()/1000 >= command.expires) throw Error('The conversation changed or the request expired. Nothing was sent.');
  }
  async function ready(command, read) {
    const deadline = Math.min(Date.now()+8000,command.expires*1000);
    let error;
    do {
      checkTarget(command);
      try { return read(); } catch(e) { error=e; }
      await new Promise(resolve=>setTimeout(resolve,200));
    } while(Date.now()<deadline);
    throw error || Error('The browser request expired.');
  }
  async function submit(command) {
    checkTarget(command);
    if (typeof command.text !== 'string' || !command.text.trim() || command.text.length > 16000) throw Error('Invalid prompt.');
    const input = await ready(command,composer);
    if (normalize(input.innerText)) throw Error('This conversation already has an unsent draft. Relay preserved it.');
    input.focus();
    if (!document.execCommand('insertText',false,command.text)) throw Error('Could not insert the prompt. Nothing was sent.');
    // Allow the app to update its editor and enable Send, then verify live elements again.
    await new Promise(resolve=>setTimeout(resolve,350));
    checkTarget(command);
    const fresh = composer();
    if (normalize(fresh.innerText) !== normalize(command.text)) throw Error('The inserted prompt could not be verified. Review the browser draft; nothing was submitted.');
    const button = await ready(command,sendButton);
    checkTarget(command);
    if(normalize(composer().innerText)!==normalize(command.text))throw Error('The draft changed before sending. Nothing was submitted.');
    button.click(); // Exactly one click. No retry, even if the page does not respond.
    return {submitted:true,url:command.url};
  }
  chrome.runtime.onMessage.addListener((message,sender,respond)=>{
    if (sender.id !== chrome.runtime.id) return;
    if (message.type === 'discover') { respond({chats:discover()}); return; }
    if (message.type === 'send') {
      submit(message.command).then(respond,error=>respond({error:error.message}));
      return true;
    }
  });
  let timer;
  const report = () => chrome.runtime.sendMessage({type:'snapshot',chats:discover()}).catch(()=>{});
  new MutationObserver(()=>{clearTimeout(timer);timer=setTimeout(report,800);}).observe(document.body,{childList:true,subtree:true});
  setInterval(report,10000);
  report();
})();
