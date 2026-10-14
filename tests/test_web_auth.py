# coding=utf-8
"""interface.check_auth:auth_method=password 时的 Basic Auth 校验。"""
import configparser
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import util  # noqa: E402
import variables as var  # noqa: E402


class FakeSettingsDb:
    def __init__(self, data=None):
        self.data = data or {}

    def get(self, section, option, fallback=None):
        return self.data.get((section, option), fallback)


@pytest.fixture
def interface(monkeypatch):
    config = configparser.ConfigParser(interpolation=None, allow_no_value=True)
    config.read(util.solve_filepath('configuration.default.ini'), encoding='utf-8')
    monkeypatch.setattr(var, 'config', config)
    monkeypatch.setattr(var, 'db', FakeSettingsDb())
    import interface as mod
    return mod


def test_configured_credentials(interface):
    var.config.set('webinterface', 'user', 'admin')
    var.config.set('webinterface', 'password', 's3cret')
    assert interface.check_auth('admin', 's3cret')
    assert not interface.check_auth('admin', 'wrong')
    assert not interface.check_auth('other', 's3cret')
    assert not interface.check_auth('admin', None)


def test_empty_config_does_not_accept_empty_credentials(interface):
    # user / password 都没配:空用户名 + 空密码以前会被放行
    assert var.config.get('webinterface', 'user') == ''
    assert not interface.check_auth('', '')


def test_web_user_with_salted_password(interface):
    import json
    hashed, salt = util.get_salted_password_hash('pw')
    var.db = FakeSettingsDb({
        ('privilege', 'web_access'): json.dumps(['alice']),
        ('user', 'alice'): json.dumps({'password': hashed, 'salt': salt}),
    })
    assert interface.check_auth('alice', 'pw')
    assert not interface.check_auth('alice', 'nope')
    assert not interface.check_auth('bob', 'pw')


def test_snapshot_version_is_commit_or_tag():
    ver = util.get_snapshot_version()
    assert ver and ver != 'unknown'  # 仓库本身是 git 检出
    assert os.getcwd()  # 不再 chdir


def test_token_page_when_no_token(interface, monkeypatch):
    # auth_method=token 且没带 token:渲染 templates/need_token.html(以前依赖旧前端构建出的模板)
    import constants
    constants.load_lang('zh_CN')
    monkeypatch.setattr(var, 'language', 'zh_CN')
    var.config.set('webinterface', 'auth_method', 'token')
    var.config.set('bot', 'username', 'dj<bot>')
    interface.bad_access_count.clear()
    try:
        rv = interface.web.test_client().get('/api/status')
        body = rv.get_data(as_text=True)
    finally:
        constants.load_lang('en_US')
    assert rv.status_code == 200
    assert '需要登录令牌' in body
    assert 'dj&lt;bot&gt;' in body  # 机器人名字转义
    assert '"!web"' in body or '!web' in body
    assert 'name="token"' in body


def test_token_grants_access(interface, monkeypatch):
    import json
    var.config.set('webinterface', 'auth_method', 'token')
    var.db = FakeSettingsDb({('web_token', 'tok123'): 'alice',
                             ('user', 'alice'): json.dumps({})})
    var.db.set = lambda *a: None
    monkeypatch.setattr(interface.web_users, 'resolve_identity', lambda user: user)
    interface.web.secret_key = 'test'
    seen = {}

    @interface.requires_auth
    def protected():
        from flask import g
        seen['identity'] = g.web_identity
        return 'ok'

    with interface.web.test_request_context('/?token=tok123'):
        assert protected() == 'ok'
    assert seen['identity'] == 'alice'
