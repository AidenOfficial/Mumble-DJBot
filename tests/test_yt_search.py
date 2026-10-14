# coding=utf-8
"""!ysearch / !yplay:走 web_search.search_youtube(yt-dlp),分页状态按用户分开。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import constants  # noqa: E402
from commands import _shared, sources  # noqa: E402


def fake_results(prefix, n):
    return [{'source': 'youtube', 'id': f'{prefix}{i}', 'url': f'https://www.youtube.com/watch?v={prefix}{i}',
             'title': f'{prefix} song {i}', 'uploader': f'{prefix} channel', 'duration': 60,
             'thumbnail': ''} for i in range(n)]


class FakeBot:
    def __init__(self):
        self.sent = []

    def send_msg(self, msg, text=None):
        self.sent.append(msg)


@pytest.fixture
def bot(monkeypatch):
    constants.load_lang('en_US')
    monkeypatch.setattr(sources, '_yt_search_state', {})
    calls = []

    def search(query, limit=20):
        calls.append((query, limit))
        if query == 'boom':
            raise RuntimeError('network down')
        if query == 'xss':
            return [{'url': 'https://www.youtube.com/watch?v=x', 'title': '<b>bold</b> & co',
                     'uploader': '<script>', 'id': 'x', 'source': 'youtube', 'duration': 1, 'thumbnail': ''}]
        return fake_results(query, 7)

    monkeypatch.setattr(sources.web_search, 'search_youtube', search)
    b = FakeBot()
    b.calls = calls
    return b


def test_search_shows_first_page_and_fills_shortlist(bot):
    sources.cmd_yt_search(bot, 'alice', None, 'ysearch', 'cats')
    assert bot.calls == [('cats', sources.YT_SEARCH_LIMIT)]
    assert 'cats song 0' in bot.sent[-1] and 'cats song 4' in bot.sent[-1]
    assert 'cats song 5' not in bot.sent[-1]
    assert [s['url'] for s in _shared.song_shortlist][0] == 'https://www.youtube.com/watch?v=cats0'
    assert all('title' in s for s in _shared.song_shortlist)


def test_next_page_is_per_user(bot):
    sources.cmd_yt_search(bot, 'alice', None, 'ysearch', 'cats')
    sources.cmd_yt_search(bot, 'bob', None, 'ysearch', 'dogs')
    sources.cmd_yt_search(bot, 'alice', None, 'ysearch', '-n')
    # alice 翻到的是自己的第二页,不受 bob 的搜索影响
    assert 'cats song 5' in bot.sent[-1] and 'dogs' not in bot.sent[-1]
    assert _shared.song_shortlist[0]['url'].endswith('cats5')
    sources.cmd_yt_search(bot, 'alice', None, 'ysearch', '-n')
    assert bot.sent[-1] == constants.tr_cli('yt_no_more')
    sources.cmd_yt_search(bot, 'bob', None, 'ysearch', '-n')
    assert 'dogs song 5' in bot.sent[-1]


def test_next_page_without_search(bot):
    sources.cmd_yt_search(bot, 'carol', None, 'ysearch', '-n')
    assert bot.sent[-1] == constants.tr_cli('yt_no_more')


def test_search_error_is_reported(bot):
    sources.cmd_yt_search(bot, 'alice', None, 'ysearch', 'boom')
    assert bot.sent[-1] == constants.tr_cli('yt_query_error')


def test_titles_are_html_escaped(bot):
    sources.cmd_yt_search(bot, 'alice', None, 'ysearch', 'xss')
    assert '&lt;b&gt;bold&lt;/b&gt; &amp; co' in bot.sent[-1]
    assert '<script>' not in bot.sent[-1]


def test_yplay_plays_first_result(bot, monkeypatch):
    played = []
    monkeypatch.setattr(sources, 'cmd_play_url', lambda b, u, t, c, url: played.append(url))
    sources.cmd_yt_play(bot, 'alice', None, 'yplay', 'cats')
    assert bot.calls == [('cats', 1)]
    assert played == ['https://www.youtube.com/watch?v=cats0']
