"""Local SmolLM3 provider with grammar-constrained Omega tool calls.

The model is served by the bundled llama.cpp process. There is no cloud API
key and no model download at runtime in the default Docker image.
"""
import json
import urllib.error
import urllib.request
import uuid

from config import config_get_by_key
import providers


def response_schema(tools):
    choices = []
    for tool in tools:
        if tool.name == 'send':
            continue
        parameters = {parameter.name: {'type': 'string'} for parameter in tool.parameters}
        choices.append({'type': 'object', 'properties': {
            'name': {'const': tool.name},
            'arguments': {'type': 'object', 'properties': parameters,
                          'required': list(parameters), 'additionalProperties': False}},
            'required': ['name', 'arguments'], 'additionalProperties': False})
    return {'type': 'object', 'properties': {
        'reply': {'type': 'string'},
        'tool_calls': {'type': 'array', 'minItems': 0, 'maxItems': 3 if choices else 0,
                       'items': {'oneOf': choices} if choices else False}},
        'required': ['reply', 'tool_calls'], 'additionalProperties': False}


def messages_for(request):
    messages = []
    for message in request.messages:
        if isinstance(message, providers.LLMToolCallMessage):
            reply = '\n'.join(str(call.arguments.get('content', '')) for call in message.calls if call.name == 'send')
            calls = [{'name': call.name, 'arguments': call.arguments} for call in message.calls if call.name != 'send']
            messages.append({'role': 'assistant', 'content': json.dumps({'reply': reply, 'tool_calls': calls})})
        elif isinstance(message, providers.LLMToolCallResponseMessage):
            messages.append({'role': 'user', 'content': f'Tool result ({message.callid}): {message.content}'})
        else:
            messages.append({'role': message.role, 'content': str(message.content)})
    instructions = ('\n/no_think\nRespond with one JSON object containing reply and tool_calls. '
                    'reply is your user-facing message. tool_calls is a list of actions with name and arguments. '
                    'For greetings and simple conversation, write the answer in reply and use an empty tool_calls list. '
                    'Use tools only when an action or observation is needed. Do not claim a tool succeeded before its result is present. '
                    'When taking an action, reply may briefly explain the next step. At most three tool calls. '
                    'When no new information needs sending during autonomous work, leave reply empty.\n'
                    'Available action tools:\n' + json.dumps([
                        {'name': tool.name, 'description': tool.description,
                         'parameters': [parameter.name for parameter in tool.parameters]} for tool in request.tools if tool.name != 'send']))
    if messages and messages[0]['role'] == 'system':
        messages[0]['content'] += instructions
    else:
        messages.insert(0, {'role': 'system', 'content': instructions})
    return messages


class SmolLM3Provider(providers.LLMProvider):
    def start(self):
        pass

    def chat(self, request):
        body = {'model': str(config_get_by_key('model', 'SmolLM3-3B')),
                'messages': messages_for(request), 'stream': False,
                'max_tokens': request.max_tokens,
                'temperature': float(config_get_by_key('localTemperature', .2)),
                'chat_template_kwargs': {'enable_thinking': False},
                'response_format': {'type': 'json_schema', 'json_schema': {
                    'name': 'omega_tools', 'strict': True, 'schema': response_schema(request.tools)}}}
        url = str(config_get_by_key('localModelURL', 'http://127.0.0.1:8089/v1')).rstrip('/') + '/chat/completions'
        http_request = urllib.request.Request(url, data=json.dumps(body).encode(),
                                              headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with urllib.request.urlopen(http_request, timeout=float(config_get_by_key('localModelTimeout', 180))) as response:
                result = json.load(response)
            choice = result['choices'][0]
            if choice.get('finish_reason') == 'length':
                return providers.LLMResponse().with_error('SmolLM3 reached the output limit; raise maxOutputToken or simplify the request')
            content = json.loads(choice['message']['content'])
            calls, reply = content['tool_calls'], content['reply']
            if not isinstance(calls, list) or len(calls) > 3 or not isinstance(reply, str):
                raise ValueError('Expected reply text and at most three tool calls')
            output = providers.LLMResponse()
            for call in calls:
                if not isinstance(call, dict) or not isinstance(call.get('arguments'), dict):
                    raise ValueError('Invalid structured tool call')
                output.add_tool_call(providers.LLMToolCall().with_id('local-' + uuid.uuid4().hex)
                                     .with_name(call['name']).with_arguments(call['arguments']))
            if reply.strip():
                output.add_tool_call(providers.LLMToolCall().with_id('local-' + uuid.uuid4().hex)
                                     .with_name('send').with_arguments({'content': reply}))
            elif not calls:
                # Preserve Omega's explicit no-op contract for quiet autonomous turns.
                output.add_tool_call(providers.LLMToolCall().with_id('local-' + uuid.uuid4().hex)
                                     .with_name('nop').with_arguments({}))
            return output
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError, IndexError) as exc:
            return providers.LLMResponse().with_error('Local SmolLM3 request failed: ' + str(exc)[:1000])


def loadOmegaPlugin():
    providers.registerLLMProvider('SmolLM3', SmolLM3Provider())
