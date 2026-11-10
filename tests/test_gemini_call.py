"""Gemini 호출: JSON 모드와, 모드가 거절될 때의 되돌림. 실제 API는 부르지 않는다."""

import itertools
import sys
import types

import pytest

from src.script_gen import generator as g


class FakeResp:
    def __init__(self, text):
        self.text = text


class Calls(list):
    """(모델, config) 호출 기록. keys에는 각 호출이 쓴 API 키가 같은 순서로 들어간다."""

    def __init__(self):
        super().__init__()
        self.keys = []


def install_fake_sdk(monkeypatch, handler):
    """google.genai를 흉내 낸다. handler(model, config) → 글, None(빈 응답) 또는 예외.

    새 SDK는 전역 설정이 없고 키마다 Client를 만든다. 어떤 키로 불렀는지도 적어 둔다.
    """
    calls = Calls()

    class Models:
        def __init__(self, key):
            self.key = key

        def generate_content(self, *, model, contents, config=None):
            calls.append((model, config))
            calls.keys.append(self.key)
            out = handler(model, config)
            if isinstance(out, Exception):
                raise out
            return FakeResp(out)

    class Client:
        def __init__(self, *, api_key=None, http_options=None):
            self.models = Models(api_key)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    sdk = types.ModuleType("google.genai")
    sdk.Client = Client
    pkg = types.ModuleType("google")
    pkg.genai = sdk
    monkeypatch.setitem(sys.modules, "google", pkg)
    monkeypatch.setitem(sys.modules, "google.genai", sdk)
    monkeypatch.setattr(g, "GEMINI_API_KEYS", ["k1", "k2"])
    monkeypatch.setattr(g, "_key_cycle", itertools.cycle(["k1", "k2"]))
    monkeypatch.setattr(g, "_json_mode", True)
    return calls


def test_json_mode_is_requested(monkeypatch):
    calls = install_fake_sdk(monkeypatch, lambda model, cfg: '{"script": "가"}')
    assert g._gemini("p") == '{"script": "가"}'
    assert calls == [(g.GEMINI_MODEL, {"response_mime_type": "application/json"})]


def test_rejected_json_mode_is_turned_off_and_retried_at_once(monkeypatch):
    def handler(model, cfg):
        if cfg:
            return ValueError("400 Unknown field response_mime_type in generation_config")
        return '{"script": "나"}'
    calls = install_fake_sdk(monkeypatch, handler)
    assert g._gemini("p") == '{"script": "나"}'
    assert [cfg for _, cfg in calls] == [{"response_mime_type": "application/json"}, None]
    assert g._json_mode is False            # 같은 실행의 다음 요청은 처음부터 일반 모드
    g._gemini("p")
    assert calls[-1][1] is None


def test_quota_errors_rotate_keys_and_models_without_dropping_json_mode(monkeypatch):
    def handler(model, cfg):
        if model == g.GEMINI_MODEL:
            return RuntimeError("429 quota exceeded")
        return '{"script": "다"}'
    calls = install_fake_sdk(monkeypatch, handler)
    assert g._gemini("p") == '{"script": "다"}'
    assert [m for m, _ in calls] == [g.GEMINI_MODEL, g.GEMINI_MODEL, g.GEMINI_FALLBACK_MODELS[0]]
    assert calls.keys == ["k1", "k2", "k1"]   # 요청마다 그 키로 만든 클라이언트를 쓴다
    assert g._json_mode is True


def test_all_failures_return_none(monkeypatch):
    install_fake_sdk(monkeypatch, lambda model, cfg: RuntimeError("503"))
    assert g._gemini("p") is None


def test_empty_response_rotates_instead_of_returning(monkeypatch):
    # 새 SDK는 차단·빈 응답에서 예외 없이 text=None을 준다. 그대로 돌려주지 않고 다음으로 간다.
    def handler(model, cfg):
        return None if model == g.GEMINI_MODEL else '{"script": "라"}'
    calls = install_fake_sdk(monkeypatch, handler)
    assert g._gemini("p") == '{"script": "라"}'
    assert [m for m, _ in calls] == [g.GEMINI_MODEL, g.GEMINI_MODEL, g.GEMINI_FALLBACK_MODELS[0]]
    assert g._json_mode is True


@pytest.mark.parametrize("raw,script", [
    ('{"script": "집값이 올랐습니다.", "youtube_title": "제목"}', "집값이 올랐습니다."),
    ('```json\n{"script": "가", // 주석\n "hashtags": ["부동산",]}\n```', "가"),
    # 제목 안의 이스케이프 안 된 따옴표. 키 위치로 잘라 읽는다.
    ('{"headline": ["가", "나"], "script": "본문입니다.", '
     '"youtube_title": "뉴시스 "서울 월세 160만원 시대"", "hashtags": ["부동산"]}', "본문입니다."),
])
def test_parse_json_repairs_common_breakage(raw, script):
    assert g._parse_json(raw)["script"] == script


def test_parse_json_gives_up_on_garbage():
    assert g._parse_json("죄송합니다. 대본을 만들 수 없습니다.") is None
