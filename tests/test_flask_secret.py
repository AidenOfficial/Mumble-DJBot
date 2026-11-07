# coding=utf-8
"""util.resolve_flask_secret:不能用公开的默认值签名会话 Cookie。"""
import configparser
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import util  # noqa: E402
import variables as var  # noqa: E402


@pytest.fixture
def config(monkeypatch):
    cfg = configparser.ConfigParser(interpolation=None, allow_no_value=True)
    cfg.read(util.solve_filepath('configuration.default.ini'), encoding='utf-8')
    monkeypatch.setattr(var, 'config', cfg)
    return cfg


def test_default_secret_is_replaced(config):
    assert config.get('webinterface', 'flask_secret') == util.DEFAULT_FLASK_SECRET
    first = util.resolve_flask_secret()
    assert first != util.DEFAULT_FLASK_SECRET
    assert len(first) >= 32
    # 每次都随机，不能退化成另一个固定值
    assert util.resolve_flask_secret() != first


@pytest.mark.parametrize('value', ['', '   '])
def test_empty_secret_is_replaced(config, value):
    config.set('webinterface', 'flask_secret', value)
    assert util.resolve_flask_secret().strip()


def test_configured_secret_is_kept(config):
    config.set('webinterface', 'flask_secret', ' my-long-random-secret ')
    assert util.resolve_flask_secret() == 'my-long-random-secret'
