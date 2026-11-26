"""WEBHOOK_HEADERS parsing."""
import pytest

from app.config import load_settings, ConfigError, _parse_webhook_headers


def test_parse_webhook_headers_json():
    headers = _parse_webhook_headers('{"Authorization": "Bearer token", "X-Custom": "value"}')
    assert headers == {"Authorization": "Bearer token", "X-Custom": "value"}


def test_parse_webhook_headers_keyvalue():
    headers = _parse_webhook_headers("Authorization=Bearer token,X-Custom=value")
    assert headers == {"Authorization": "Bearer token", "X-Custom": "value"}


def test_parse_webhook_headers_keyvalue_with_spaces():
    headers = _parse_webhook_headers(" Authorization = Bearer token , X-Custom = value ")
    assert headers == {"Authorization": "Bearer token", "X-Custom": "value"}


def test_parse_webhook_headers_empty():
    assert _parse_webhook_headers(None) is None
    assert _parse_webhook_headers("") is None
    assert _parse_webhook_headers("   ") is None


def test_parse_webhook_headers_invalid_json():
    with pytest.raises(ConfigError, match="WEBHOOK_HEADERS JSON 格式错误"):
        _parse_webhook_headers('{"invalid": json}')


def test_parse_webhook_headers_json_not_object():
    with pytest.raises(ConfigError, match="WEBHOOK_HEADERS JSON 必须是对象，不能是数组"):
        _parse_webhook_headers('["array"]')


def test_parse_webhook_headers_invalid_keyvalue():
    with pytest.raises(ConfigError, match="WEBHOOK_HEADERS 键值对格式错误"):
        _parse_webhook_headers("invalid_no_equals")


def test_load_settings_with_webhook():
    settings = load_settings({
        "WEBHOOK_URL": "https://example.com/webhook",
        "WEBHOOK_HEADERS": '{"Authorization": "Bearer token"}',
        "WEBHOOK_TEMPLATE": '{"text": "test"}',
    })
    assert settings.webhook_url == "https://example.com/webhook"
    assert settings.webhook_headers == {"Authorization": "Bearer token"}
    assert settings.webhook_template == '{"text": "test"}'


def test_load_settings_webhook_optional():
    settings = load_settings({})
    assert settings.webhook_url is None
    assert settings.webhook_headers is None
    assert settings.webhook_template is None


@pytest.mark.parametrize("template", ['{"text": broken}', '["not", "an", "object"]'])
def test_invalid_webhook_template_fails_at_load(template):
    with pytest.raises(ConfigError, match="WEBHOOK_TEMPLATE"):
        load_settings({"WEBHOOK_TEMPLATE": template})
