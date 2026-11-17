"""Direct CLI configuration and generation event parsing."""
import json

from common import api_base_url, write_json

APIS = {'openai': 'openai-responses', 'anthropic': 'anthropic-messages',
        'gemini': 'google-generative-ai'}
IMAGE_TOOLS = {'pi': 'read', 'codex': 'view_image', 'claude': 'Read'}
SESSION_GLOBS = {'pi': '.pi/agent/sessions/*/*.jsonl',
                 'codex': '.codex/sessions/**/rollout-*.jsonl',
                 'claude': '.claude/projects/*/*.jsonl'}


def generation_config(config, agent='pi', provider=None):
    provider = provider or ('anthropic' if agent == 'claude' else 'openai')
    required = {'codex': 'openai', 'claude': 'anthropic'}.get(agent)
    if required and provider != required:
        raise ValueError(f'{agent} requires --provider {required}')
    prefix = provider.upper()
    values = {}
    for field in ('API_KEY', 'MODEL'):
        name = prefix + '_' + field
        values[field.lower()] = config.get(name, '').strip()
        if not values[field.lower()]:
            raise ValueError(f'Set {name} in .env')
    result = dict(values, agent=agent, provider=provider, api=APIS[provider],
                  base_url=api_base_url(config, prefix),
                  reasoning_effort=config.get('GENERATION_REASONING_EFFORT', 'medium').strip(),
                  context_window=int(config.get('GENERATION_CONTEXT_WINDOW', '262144')),
                  max_output_tokens=int(config.get('GENERATION_MAX_OUTPUT_TOKENS', '32768')))
    if result['context_window'] <= 0 or result['max_output_tokens'] <= 0:
        raise ValueError('Generation token limits must be positive')
    return result


def pi_models(selected):
    model = {'id': selected['model'], 'name': selected['model'], 'reasoning': True,
             'input': ['text', 'image'], 'contextWindow': selected['context_window'],
             'maxTokens': selected['max_output_tokens'],
             'cost': {'input': 0, 'output': 0, 'cacheRead': 0, 'cacheWrite': 0}}
    levels = {level: level for level in ('minimal', 'low', 'medium', 'high', 'xhigh', 'max')}
    if selected['provider'] == 'anthropic':
        model['compat'] = {'forceAdaptiveThinking': True}
        levels['minimal'] = 'low'
        levels['off'] = None
    elif selected['provider'] == 'gemini':
        levels.update(off=None, xhigh='high', max='high')
    else:
        levels['off'] = 'none'
    model['thinkingLevelMap'] = levels
    models = {'providers': {'video2code': {'name': 'video2code', 'baseUrl': selected['base_url'],
              'api': selected['api'], 'apiKey': selected['api_key'], 'models': [model]}}}
    settings = {'defaultProvider': 'video2code', 'defaultModel': selected['model'],
                'compaction': {'enabled': True, 'reserveTokens': 16384, 'keepRecentTokens': 20000}}
    if selected['reasoning_effort']:
        settings['defaultThinkingLevel'] = selected['reasoning_effort']
    return models, settings


def prepare(selected, home):
    agent = selected['agent']
    settings = {k: selected[k] for k in ('reasoning_effort', 'context_window', 'max_output_tokens')}
    if agent == 'pi':
        models, settings = pi_models(selected)
        directory = home / '.pi/agent'
        directory.mkdir(parents=True)
        write_json(directory / 'models.json', models)
        (directory / 'models.json').chmod(0o600)
        write_json(directory / 'settings.json', settings)
        settings = dict(settings, context_window=selected['context_window'],
                        max_output_tokens=selected['max_output_tokens'])
        env = {'PI_CODING_AGENT_DIR': '/home/agent/.pi/agent'}
        command = ['pi', '-p', '--mode', 'json']
    elif agent == 'codex':
        directory = home / '.codex'
        directory.mkdir(parents=True)
        settings.update(approval_policy='never', sandbox_mode='danger-full-access',
                        max_output_tokens=None)
        values = {'model': selected['model'], 'model_provider': 'video2code',
                  'model_context_window': selected['context_window'],
                  'approval_policy': 'never', 'sandbox_mode': 'danger-full-access',
                  'web_search': 'disabled'}
        if selected['reasoning_effort']:
            values['model_reasoning_effort'] = selected['reasoning_effort']
        text = '\n'.join(f'{key} = {json.dumps(value)}' for key, value in values.items())
        text += ('\n\n[features]\napps = false\nmulti_agent = false\n'
                 '\n[model_providers.video2code]\nname = "video2code"\n'
                 f'base_url = {json.dumps(selected["base_url"])}\n'
                 'wire_api = "responses"\nenv_key = "OPENAI_API_KEY"\n'
                 'requires_openai_auth = false\nsupports_websockets = false\n')
        (directory / 'config.toml').write_text(text, encoding='utf-8')
        env = {'CODEX_HOME': '/home/agent/.codex', 'OPENAI_API_KEY': selected['api_key']}
        command = ['codex', 'exec', '--json', '--skip-git-repo-check', '--color', 'never']
    else:
        directory = home / '.claude'
        directory.mkdir(parents=True)
        env = {'CLAUDE_CONFIG_DIR': '/home/agent/.claude',
               'ANTHROPIC_API_KEY': selected['api_key'], 'ANTHROPIC_BASE_URL': selected['base_url'],
               'ANTHROPIC_MODEL': selected['model'], 'CLAUDE_CODE_SUBAGENT_MODEL': selected['model'],
               'CLAUDE_CODE_MAX_CONTEXT_TOKENS': str(selected['context_window']),
               'CLAUDE_CODE_MAX_OUTPUT_TOKENS': str(selected['max_output_tokens']),
               'IS_SANDBOX': '1', 'DISABLE_AUTOUPDATER': '1',
               'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1'}
        for name in ('OPUS', 'SONNET', 'HAIKU'):
            env[f'ANTHROPIC_DEFAULT_{name}_MODEL'] = selected['model']
        command = ['claude', '-p', '--output-format', 'stream-json', '--verbose',
                   '--dangerously-skip-permissions', '--model', selected['model']]
        if selected['reasoning_effort']:
            command += ['--effort', selected['reasoning_effort']]
    return {'command': command, 'env': env, 'settings': settings,
            'session_glob': SESSION_GLOBS[agent]}


def summarize(agent, events, returncode):
    result = {'exit_code': returncode}
    last, terminal = None, False
    for line in events.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        last = event.get('type')
        session = event.get('sessionId') or event.get('session_id') or event.get('thread_id')
        if session:
            result['session_id'] = session
        if agent == 'pi' and last == 'agent_end':
            terminal = True
            assistant = next((m for m in reversed(event.get('messages') or [])
                              if isinstance(m, dict) and m.get('role') == 'assistant'), None)
            error = (assistant.get('errorMessage') or 'Assistant turn ended with an error') \
                if assistant and assistant.get('stopReason') == 'error' else None
        elif agent == 'codex' and last in ('turn.completed', 'turn.failed', 'error'):
            terminal = True
            error = (event.get('error') or {}).get('message') if last == 'turn.failed' else event.get('message')
            error = error or ('Codex turn failed' if last != 'turn.completed' else None)
        elif agent == 'claude' and last == 'result':
            terminal = True
            error = '\n'.join(map(str, event.get('errors') or [])) or event.get('result') \
                or 'Claude turn failed' if event.get('is_error') else None
        else:
            continue
        if error:
            result['agent_error'] = error
        else:
            result.pop('agent_error', None)
    result.update(completed=returncode == 0 and 'agent_error' not in result
                  and (terminal or agent == 'pi'), last_event_type=last)
    if returncode == 0 and not result['completed'] and 'agent_error' not in result:
        result['agent_error'] = 'CLI did not emit a completed turn'
    return result
