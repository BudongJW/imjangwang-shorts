"""Gemini 호출: JSON 모드와, 모드가 거절될 때의 되돌림. 실제 API는 부르지 않는다."""

import itertools
import sys
import types

import pytest

from src.script_gen import generator as g


class FakeResp:
    def __init__(self, text):
        self.text = text


def install_fake_sdk(monkeypatch, handler):
    """google.generativeai를 흉내 낸다. handler(model, generation_config) → 글 또는 예외."""
    calls = []

    class Model:
        def __init__(self, name, generation_config=None):
            self.name, self.cfg = name, generation_config

        def generate_content(self, prompt):
            calls.append((self.name, self.cfg))
            out = handler(self.name, self.cfg)
            if isinstance(out, Exception):
                raise out
            return FakeResp(out)

    sdk = types.ModuleType("google.generativeai")
    sdk.configure = lambda api_key=None: None
    sdk.GenerativeModel = Model
    pkg = types.ModuleType("google")
    pkg.generativeai = sdk
    monkeypatch.setitem(sys.modules, "google", pkg)
    monkeypatch.setitem(sys.modules, "google.generativeai", sdk)
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
    assert g._json_mode is True


def test_all_failures_return_none(monkeypatch):
    install_fake_sdk(monkeypatch, lambda model, cfg: RuntimeError("503"))
    assert g._gemini("p") is None


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
