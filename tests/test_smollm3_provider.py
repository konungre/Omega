"""Local-provider boundary cases; live-model validation is a separate smoke check."""
import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import patch
import pytest
import providers

spec = importlib.util.spec_from_file_location('omega_smollm3', Path(__file__).resolve().parents[1] / 'providers/smollm3.py')
local = importlib.util.module_from_spec(spec)
spec.loader.exec_module(local)


def request():
    value = providers.LLMRequest()
    value.max_tokens = 256
    for name, params in [('send', ['content']), ('nop', []), ('ona-input', ['narsese'])]:
        tool = providers.LLMTool().with_name(name).with_description(name)
        for parameter in params:
            tool.add_parameter(providers.LLMToolParameter().with_name(parameter))
        value.add_tool(tool)
    value.add_message(providers.LLMMessage().with_role('user').with_content('Hello'))
    return value


def completion(content, reason='stop'):
    return io.BytesIO(json.dumps({'choices': [{'finish_reason': reason, 'message': {'content': json.dumps(content)}}]}).encode())


def test_conversation_reply_uses_normal_send_and_needs_no_action():
    with patch.object(local.urllib.request, 'urlopen', return_value=completion({'reply': 'Hello!', 'tool_calls': []})):
        response = providers._validate_response(request(), local.SmolLM3Provider().chat(request()))
    assert not response.is_error()
    assert [(call.name, call.arguments) for call in response.calls] == [('send', {'content': 'Hello!'})]


def test_actions_and_reply_remain_distinct_validated_calls():
    payload = {'reply': 'I will inspect the reasoner.', 'tool_calls': [{'name': 'ona-input', 'arguments': {'narsese': '<bird --> animal>?'}}]}
    with patch.object(local.urllib.request, 'urlopen', return_value=completion(payload)):
        response = providers._validate_response(request(), local.SmolLM3Provider().chat(request()))
    assert [call.name for call in response.calls] == ['ona-input', 'send']
    assert all(not call.is_error() for call in response.calls)


def test_quiet_autonomous_turn_is_explicit_noop():
    with patch.object(local.urllib.request, 'urlopen', return_value=completion({'reply': '', 'tool_calls': []})):
        response = local.SmolLM3Provider().chat(request())
    assert response.calls[0].name == 'nop'


@pytest.mark.parametrize('payload,reason', [({'reply': 'Incomplete', 'tool_calls': []}, 'length'), ({'reply': 7, 'tool_calls': []}, 'stop'), ({'tool_calls': []}, 'stop')])
def test_truncation_and_malformed_content_are_failed_results(payload, reason):
    with patch.object(local.urllib.request, 'urlopen', return_value=completion(payload, reason)):
        assert local.SmolLM3Provider().chat(request()).is_error()


def test_timeout_is_a_failed_provider_result():
    with patch.object(local.urllib.request, 'urlopen', side_effect=TimeoutError('local inference deadline')):
        assert local.SmolLM3Provider().chat(request()).is_error()
