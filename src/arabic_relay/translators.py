"""What the user can choose for English prompts: installed tools, their models, effort levels and sign-in state."""
import json
import os
import subprocess

from . import locate, store

NAMES = {'codex': 'Codex', 'claude': 'Claude Code'}
EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')  # Codex's 'ultra' delegates tasks; never offered
CLAUDE_MODELS = (('fable', 'Fable'), ('opus', 'Opus'), ('sonnet', 'Sonnet'), ('haiku', 'Haiku'))
CACHE = {}


def run(command, timeout):
    env = os.environ.copy()
    for key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'ANTHROPIC_API_KEY'):
        env.pop(key, None)
    with locate.external_runtime():
        return subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout,
                              env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def parse_codex_models(text):
    """`codex debug models` JSON → listed models with their efforts; anything unexpected → [] (Default and Custom… still work)."""
    try:
        models = []
        for model in json.loads(text)['models']:
            if model.get('visibility') != 'list':
                continue
            offered = {level['effort'] for level in model['supported_reasoning_levels']}
            if not isinstance(model['slug'], str) or not isinstance(model.get('display_name', ''), str):
                raise TypeError
            models.append({'id': model['slug'], 'name': model.get('display_name') or model['slug'],
                           'efforts': [effort for effort in EFFORTS if effort in offered]})
        return models
    except (ValueError, KeyError, TypeError, AttributeError):
        return []


def codex_models(executable):
    try:
        done = run([str(executable), 'debug', 'models'], 10)
    except (OSError, subprocess.SubprocessError):
        return []
    return [] if done.returncode else parse_codex_models(done.stdout)


def codex_signed_in(executable):
    try:
        done = run([str(executable), 'login', 'status'], 10)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.returncode == 0 and 'Logged in' in (done.stdout + done.stderr)


def catalog(refresh=False):
    if refresh or not CACHE:
        codex, claude = locate.tool_path('codex'), locate.tool_path('claude')
        # Build first, then swap: a request during the refresh keeps seeing the old list.
        CACHE.update({
            'codex': {'installed': codex is not None, 'path': str(codex) if codex else None,
                      'signedIn': codex_signed_in(codex) if codex else None, 'models': codex_models(codex) if codex else []},
            # Claude Code has no sign-in query; a failed call records it (translate.py).
            'claude': {'installed': claude is not None, 'path': str(claude) if claude else None,
                       'signedIn': False if str(store.get_setting('claude_signed_out')) == '1' else None,
                       'models': [{'id': slug, 'name': name, 'efforts': list(EFFORTS)} for slug, name in CLAUDE_MODELS]},
            'efforts': list(EFFORTS)})
    return CACHE


def label(choice):
    """`Codex · GPT-6.1-Sol · low`: the line next to Prepare English prompt."""
    # Read the cache only: status must never wait on `codex`. Until Settings loads the list, the slug is shown.
    names = {model['id']: model['name'] for model in CACHE.get(choice['tool'], {}).get('models', [])}
    model = names.get(choice['model'], choice['model']) if choice['model'] else 'Default'
    return f"{NAMES[choice['tool']]} · {model} · {choice['effort']}"
