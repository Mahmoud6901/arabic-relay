/* Private, exact conversation routes shared by discovery and sending. */
const RelayAddress = (() => {
  const uuid = '[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}';
  function parse(value) {
    try {
      const u = new URL(value);
      if (u.protocol !== 'https:' || u.search || u.hash || u.username || u.password || u.port) return null;
      const provider = {'chatgpt.com':'chatgpt','claude.ai':'claude'}[u.hostname];
      const patterns = provider === 'chatgpt' ? [new RegExp(`^/(?:g/[A-Za-z0-9_-]+/)?c/(${uuid})$`)] : provider === 'claude' ?
        [new RegExp(`^/chat/(${uuid})$`), /^\/cowork\/(cse_[A-Za-z0-9]{10,80})$/, /^\/code\/(session_[A-Za-z0-9]{10,80})$/] : [];
      for (const pattern of patterns) {
        const m = u.pathname.match(pattern);
        if (m) return {provider, url:u.href, id:provider+':'+(new RegExp(`^${uuid}$`).test(m[1])?m[1].toLowerCase():m[1])};
      }
    } catch {}
    return null;
  }
  return {parse};
})();
if (typeof module !== 'undefined') module.exports = RelayAddress;
