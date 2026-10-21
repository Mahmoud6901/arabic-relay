"""On-demand Windows accessibility bridge. No background discovery or blind keystrokes."""
import os
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlparse

LOCK = threading.Lock()
TARGETS = {}
NAMES = {'chatgpt': 'ChatGPT Desktop', 'claude': 'Claude Desktop'}


def _uia():
    import clr
    framework = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/WPF'
    for assembly in ('WindowsBase', 'UIAutomationTypes', 'UIAutomationClient'):
        clr.AddReference(str(framework / (assembly + '.dll')))
    from System.Windows.Automation import AutomationElement, ControlType, TreeScope, Condition, ValuePattern, InvokePattern
    from System.Diagnostics import Process
    return AutomationElement, ControlType, TreeScope, Condition, ValuePattern, InvokePattern, None, Process


def chat_url(provider, value, allow_new=True):
    """Only private conversation URLs from the correct provider establish a destination."""
    try:
        url = urlparse(value)
        host = 'chatgpt.com' if provider == 'chatgpt' else 'claude.ai'
        if url.scheme != 'https' or url.netloc != host or url.query or url.fragment:
            return None
        path = url.path.rstrip('/') or '/'
        if allow_new and path in ('/', '/new'):
            return 'new'
        prefix = '/c/' if provider == 'chatgpt' else '/chat/'
        import uuid
        if path.startswith(prefix) and str(uuid.UUID(path[len(prefix):])) == path[len(prefix):].lower():
            return value.rstrip('/')
    except (ValueError, TypeError, AttributeError):
        pass
    return None


def _elements(root):
    AE, CT, TS, Condition, VP, IP, LP, Process = _uia()
    return list(root.FindAll(TS.Descendants, Condition.TrueCondition))


def _controls(nodes, control_type):
    for node in nodes:
        try:
            if node.Current.ControlType == control_type and not node.Current.IsOffscreen:
                yield node
        except Exception:
            # React may remove an unrelated control while accessibility is enumerating.
            continue


def _pattern(element, pattern):
    try:
        return element.GetCurrentPattern(pattern.Pattern)
    except Exception:
        return None


def _value(element):
    AE, CT, TS, Condition, VP, IP, LP, Process = _uia()
    for pattern in (VP, LP):
        if pattern is None:
            continue
        current = _pattern(element, pattern)
        if current is not None:
            try:
                return str(current.Current.Value or '')
            except Exception:
                pass
    return ''


def _document(provider, nodes):
    AE, CT, TS, Condition, VP, IP, LP, Process = _uia()
    if provider == 'chatgpt':
        documents = [node for node in _controls(nodes, CT.Document)
                     if _value(node) == 'app://-/index.html' and str(node.Current.Name) == 'ChatGPT']
        homes = [node for node in _controls(nodes, CT.Group)
                 if '[container-name:home-main-content]' in str(node.Current.ClassName)
                 and not node.Current.IsOffscreen]
        if len(documents) == 1 and len(homes) == 1:
            return homes[0], 'new'
        raise ValueError('ChatGPT is not showing its empty New chat screen. Nothing was sent.')
    documents = [(node, chat_url(provider, _value(node))) for node in _controls(nodes, CT.Document)]
    documents = [(node, url) for node, url in documents if url]
    if len(documents) != 1:
        raise ValueError('The desktop app does not expose a verifiable chat address to Windows accessibility.')
    return documents[0]


def _draft(provider, composer):
    value = _value(composer)
    if provider == 'chatgpt' and value == str(composer.Current.Name) + '\n':
        # Chromium includes the placeholder in this app's empty ProseMirror value.
        AE, CT, TS, Condition, VP, IP, LP, Process = _uia()
        children = list(composer.FindAll(TS.Descendants, Condition.TrueCondition))
        if (len(children) == 2 and str(children[0].Current.Name) == str(composer.Current.Name)
                and str(children[1].Current.ClassName) == 'ProseMirror-trailingBreak'
                and str(children[1].Current.Name) == '\n'):
            return ''
    return value


def _composer(nodes, provider=None):
    AE, CT, TS, Condition, VP, IP, LP, Process = _uia()
    candidates = []
    for node in _controls(nodes, CT.Edit):
        if not node.Current.IsEnabled:
            continue
        label = str(node.Current.Name or '').lower()
        if 'search' in label:
            continue
        if provider == 'chatgpt' and label.strip() != 'work with chatgpt':
            # The unified app can also expose editable fields inside its browser panel.
            # Only its own observed composer label identifies the intended message box.
            continue
        pattern = _pattern(node, VP)
        if pattern is not None and not pattern.Current.IsReadOnly:
            candidates.append(node)
    if len(candidates) != 1:
        raise ValueError('The desktop app does not expose one writable message box. Sending is unavailable.')
    return candidates[0]


def _button(nodes, labels):
    AE, CT, TS, Condition, VP, IP, LP, Process = _uia()
    buttons = [node for node in _controls(nodes, CT.Button)
               if str(node.Current.Name or '').strip().lower() in labels]
    if len(buttons) != 1 or _pattern(buttons[0], IP) is None:
        raise ValueError('The desktop app does not expose an unambiguous action button. Sending is unavailable.')
    return buttons[0]


def _roots():
    AE, CT, TS, Condition, VP, IP, LP, Process = _uia()
    roots = {'chatgpt': [], 'claude': []}
    for window in AE.RootElement.FindAll(TS.Children, Condition.TrueCondition):
        try:
            if window.Current.ControlType != CT.Window or window.Current.IsOffscreen:
                continue
            process = Process.GetProcessById(window.Current.ProcessId)
            path = Path(str(process.MainModule.FileName)).resolve()
            if path.name.lower() == 'claude.exe' and path.is_relative_to(Path(os.environ['LOCALAPPDATA']) / 'AnthropicClaude'):
                roots['claude'].append(window)
            if path.name.lower() == 'chatgpt.exe' and path.is_relative_to(Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'WindowsApps') and any(part.startswith('OpenAI.') for part in path.parts):
                roots['chatgpt'].append(window)
        except Exception:
            continue
    return roots


def catalog():
    projects = []
    statuses = []
    with LOCK:
        bridge_error = None
        try:
            roots = _roots() if os.environ.get('ARABIC_RELAY_NATIVE') == '1' else {}
        except Exception:
            roots = {}
            bridge_error = 'Windows accessibility support could not load. Desktop sending is unavailable.'
        for provider, name in NAMES.items():
            project = {'id': 'desktop:' + provider, 'title': name, 'workspaceRoot': 'Desktop application',
                       'desktop': True, 'threads': []}
            reason = bridge_error or ('Open the desktop app, sign in, then check again in Settings.'
                     if os.environ.get('ARABIC_RELAY_NATIVE') == '1' else 'Open native Arabic Relay to check desktop chat access.')
            try:
                windows = roots.get(provider, [])
                if len(windows) != 1:
                    raise ValueError(reason if not windows else 'Keep one chat window open, then check again.')
                root = windows[0]
                nodes = _elements(root)
                _composer(nodes, provider)
                _button(nodes, {'new', 'new chat', 'start new chat'})
                token = secrets.token_urlsafe(20)
                TARGETS[token] = {'provider': provider, 'root': root, 'url': 'new',
                                  'title': 'New chat', 'created': time.time()}
                project['threads'].append({'id': token, 'title': 'New chat', 'status': 'ready', 'runtimeMode': 'desktop'})
                reason = 'Ready. Send starts a new conversation.'
            except ValueError as error:
                reason = str(error)
            except Exception:
                reason = 'Desktop chat controls are unavailable. Enable app accessibility and check again.'
            project['connectionStatus'] = reason
            statuses.append({'name': name, 'ready': bool(project['threads']), 'message': reason})
            projects.append(project)
        cutoff = time.time() - 1800
        for token in list(TARGETS):
            if TARGETS[token]['created'] < cutoff:
                del TARGETS[token]
    return {'projects': projects, 'apps': statuses}


def validate(target):
    item = TARGETS.get(target['threadId'])
    if (not item or item['created'] < time.time() - 1800
            or target['projectId'] != 'desktop:' + item['provider'] or item['url'] != 'new'):
        raise ValueError('Refresh desktop chats and select the destination again.')
    return item


def send(target, text):
    """Verify the prepared new chat and exact draft before invoking Send."""
    with LOCK:
        item = validate(target)
        if not item.get('prepared'):
            raise ValueError('The new desktop chat is not ready. Refresh destinations and try again.')
        AE, CT, TS, Condition, VP, IP, LP, Process = _uia()
        roots = _roots().get(item['provider'], [])
        if len(roots) != 1 or roots[0].Current.NativeWindowHandle != item['root'].Current.NativeWindowHandle:
            raise ValueError('The desktop window changed. Refresh destinations and try again.')
        root = roots[0]
        nodes = _elements(root)
        document, actual_url = _document(item['provider'], nodes)
        if actual_url != 'new':
            raise ValueError('The desktop app did not open a new chat. Nothing was sent.')
        composer = _composer(_elements(document), item['provider'])
        if _draft(item['provider'], composer).strip():
            raise ValueError('The new chat has an unsent draft. Nothing was sent.')
        _pattern(composer, VP).SetValue(text)
        # Read the live replacement editor, not the element React may have just removed.
        nodes = _elements(root)
        document, checked_url = _document(item['provider'], nodes)
        if checked_url != actual_url:
            raise ValueError('The chat changed before sending. Review the app; Relay did not click Send.')
        composer = _composer(_elements(document), item['provider'])
        if _value(composer).replace('\r\n', '\n') != text.replace('\r\n', '\n'):
            raise ValueError('The desktop draft could not be verified. Review it in the app; Relay did not click Send.')
        button = _button(_elements(document), {'send', 'send message', 'send prompt'})
        if not button.Current.IsEnabled:
            raise ValueError('The desktop Send button is unavailable. Review the app; no automatic retry was made.')
        _pattern(button, IP).Invoke()
        # UI automation cannot supply a provider API receipt. Report submission, never confirmed delivery.
        return {'delivery': 'desktop_submit', 'messageId': 'desktop:' + secrets.token_urlsafe(12),
                'note': 'The verified desktop Send action was invoked. Check the app for its response.'}


def prepare(target):
    """Open New chat and wait for verified navigation as part of the Send action."""
    with LOCK:
        item = validate(target)
        item['prepared'] = False
        AE, CT, TS, Condition, VP, IP, LP, Process = _uia()
        roots = _roots().get(item['provider'], [])
        if len(roots) != 1 or roots[0].Current.NativeWindowHandle != item['root'].Current.NativeWindowHandle:
            raise ValueError('The desktop window changed. Refresh destinations and try again.')
        nodes = _elements(roots[0])
        if _draft(item['provider'], _composer(nodes, item['provider'])).strip():
            raise ValueError('The desktop app has an unsent draft. Finish or save it before sending from Relay.')
        _pattern(_button(nodes, {'new', 'new chat', 'start new chat'}), IP).Invoke()
        deadline = time.monotonic() + 5
        while True:
            try:
                document, url = _document(item['provider'], _elements(roots[0]))
                if url == 'new':
                    composer = _composer(_elements(document), item['provider'])
                    if _draft(item['provider'], composer).strip():
                        raise ValueError('The new chat has an unsent draft. Nothing was sent.')
                    item['prepared'] = True
                    return
            except ValueError:
                if time.monotonic() >= deadline:
                    raise
            if time.monotonic() >= deadline:
                raise ValueError('The desktop app did not open a verifiable new chat. Nothing was sent.')
            time.sleep(0.1)
