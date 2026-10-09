"""日志面板:内存缓冲、磁盘历史(重启后读回)、调试开关、API 权限。"""
import configparser
import json
import logging
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import variables as var  # noqa: E402
from bot import logbuffer  # noqa: E402
from tests.test_web_users import EMAIL_HEADER, identity_auth  # noqa: E402


def make_logger(buf, name='test-bot'):
    logger = logging.getLogger(name)
    logger.handlers = [buf]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    return logger


class BufferTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.buf = logbuffer.LogBuffer(capacity=50)
        self.log = make_logger(self.buf)

    def tearDown(self):
        self.tmp.cleanup()

    def test_query_filters_by_seq_level_and_text(self):
        self.log.debug("noise")
        self.log.info("song started: Foo")
        self.log.warning("download slow")
        try:
            raise ValueError("boom")
        except ValueError:
            self.log.exception("player crashed")

        everything = self.buf.query()
        self.assertEqual([e['level'] for e in everything['entries']], ['DEBUG', 'INFO', 'WARNING', 'ERROR'])
        self.assertIn('ValueError: boom', everything['entries'][-1]['exc'])

        warn = self.buf.query(min_level='WARNING')
        self.assertEqual([e['msg'] for e in warn['entries']], ['download slow', 'player crashed'])
        # 增量:after 之后只有新的;last_seq 总是最新序号,即使被过滤掉
        self.assertEqual(self.buf.query(after=everything['last_seq'])['entries'], [])
        self.log.debug("later")
        inc = self.buf.query(after=everything['last_seq'], min_level='INFO')
        self.assertEqual(inc['entries'], [])
        self.assertEqual(inc['last_seq'], everything['last_seq'] + 1)
        # 关键词也搜 traceback
        self.assertEqual(len(self.buf.query(q='BOOM')['entries']), 1)

    def test_capacity_and_limit(self):
        for i in range(80):
            self.log.info("line %d", i)
        result = self.buf.query(limit=10)
        self.assertEqual(result['entries'][-1]['msg'], 'line 79')
        self.assertEqual(len(result['entries']), 10)
        self.assertTrue(result['truncated'])
        self.assertEqual(len(self.buf.query(limit=1000)['entries']), 50)

    def test_bad_format_args_do_not_raise(self):
        self.log.info("%d items", "not a number")
        self.assertEqual(len(self.buf.query()['entries']), 1)

    def test_history_survives_restart(self):
        path = os.path.join(self.tmp.name, 'logs', 'bot-log.jsonl')
        self.log.info("early startup line")  # 接上历史之前的记录也要补写进去
        self.buf.attach_history(path)
        self.log.critical("watchdog: exiting")
        first_run = self.buf.run_id

        # 模拟重启:新进程的新缓冲
        buf2 = logbuffer.LogBuffer(capacity=50)
        log2 = make_logger(buf2, 'test-bot-2')
        log2.info("starting again")
        buf2.attach_history(path)
        entries = buf2.query()['entries']
        self.assertEqual([e['msg'] for e in entries],
                         ['early startup line', 'watchdog: exiting', 'starting again'])
        self.assertEqual([e['run'] for e in entries][:2], [first_run, first_run])
        self.assertEqual([e['seq'] for e in entries], [1, 2, 3])

    def test_history_tolerates_torn_last_line(self):
        path = os.path.join(self.tmp.name, 'bot-log.jsonl')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(json.dumps({'ts': 1, 'level': 'INFO', 'name': 'bot', 'msg': 'ok', 'run': 'old'}) + '\n')
            f.write('{"ts": 2, "level": "ERR')  # 进程被杀时写了半行
        self.buf.attach_history(path)
        self.assertEqual([e['msg'] for e in self.buf.query()['entries']], ['ok'])

    def test_history_rotates(self):
        path = os.path.join(self.tmp.name, 'bot-log.jsonl')
        self.buf.attach_history(path)
        old = logbuffer.HISTORY_MAX_BYTES
        logbuffer.HISTORY_MAX_BYTES = 2000
        try:
            for i in range(100):
                self.log.info("x" * 50)
        finally:
            logbuffer.HISTORY_MAX_BYTES = old
        self.assertTrue(os.path.exists(path + '.1'))
        self.assertLess(os.path.getsize(path), 2500)

    def test_third_party_tap_only_takes_warnings(self):
        tap = logbuffer._RootTap(self.buf)
        lib = logging.getLogger('PyMumbleTest')
        lib.addHandler(tap)
        lib.setLevel(logging.DEBUG)
        try:
            lib.info("chatty")
            lib.warning("connection lost")
        finally:
            lib.removeHandler(tap)
        self.assertEqual([e['msg'] for e in self.buf.query()['entries']], ['connection lost'])


class InstallTest(unittest.TestCase):
    def test_install_keeps_third_party_warnings_on_stderr(self):
        import io
        root = logging.getLogger()
        saved_root = root.handlers[:]
        bot_logger = logging.getLogger('test-install-bot')
        saved_buffer = logbuffer.buffer
        logbuffer.buffer = logbuffer.LogBuffer()
        root.handlers = []
        try:
            logbuffer.install(bot_logger)
            fallback = [h for h in root.handlers if isinstance(h, logging.StreamHandler)
                        and not isinstance(h, logbuffer._RootTap)]
            self.assertEqual(len(fallback), 1)
            stream = io.StringIO()
            fallback[0].setStream(stream)
            logging.getLogger('PyMumbleInstallTest').warning("udp lost")
            self.assertIn('udp lost', stream.getvalue())
            self.assertEqual([e['msg'] for e in logbuffer.buffer.query()['entries']], ['udp lost'])
        finally:
            root.handlers = saved_root
            bot_logger.handlers = []
            logbuffer.buffer = saved_buffer


class DebugToggleTest(unittest.TestCase):
    def test_debug_on_and_off_restores_level(self):
        bot_logger = logging.getLogger('bot')
        saved = bot_logger.level
        bot_logger.setLevel(logging.INFO)
        buf = logbuffer.LogBuffer()
        try:
            buf.set_debug(True, minutes=5)
            self.assertEqual(bot_logger.level, logging.DEBUG)
            self.assertIsNotNone(buf.state()['debug_until'])
            buf.set_debug(True, minutes=5)  # 再开一次不会把"原级别"记成 DEBUG
            buf.set_debug(False)
            self.assertEqual(bot_logger.level, logging.INFO)
            self.assertIsNone(buf.state()['debug_until'])
        finally:
            buf.set_debug(False)
            bot_logger.setLevel(saved)


class ApiTest(unittest.TestCase):
    def setUp(self):
        from flask import Flask
        import web_logs
        import web_users
        self.tmp = tempfile.TemporaryDirectory()
        self._saved = {n: getattr(var, n, None) for n in ('config', 'user_db')}
        self.config = configparser.ConfigParser(interpolation=None)
        self.config.read_dict({'webinterface': {'access_email_header': EMAIL_HEADER,
                                                'access_team_domain': '', 'access_aud': '',
                                                'log_viewers': ''}})
        var.config = self.config
        var.user_db = web_users.UserDatabase(os.path.join(self.tmp.name, 'settings.db'))
        self.buf = logbuffer.LogBuffer()
        self._saved_buffer = web_logs.buffer
        web_logs.buffer = self.buf
        self.log = make_logger(self.buf, 'bot.test-api')
        app = Flask(__name__)
        app.register_blueprint(web_logs.create_blueprint(identity_auth))
        app.register_blueprint(web_users.create_blueprint(identity_auth))
        self.client = app.test_client()

    def tearDown(self):
        import web_logs
        web_logs.buffer = self._saved_buffer
        for k, v in self._saved.items():
            setattr(var, k, v)
        self.tmp.cleanup()

    def test_fetch_and_download(self):
        self.log.info("hello")
        self.log.error("bad thing")
        data = self.client.get('/api/logs?level=ERROR').get_json()
        self.assertEqual([e['msg'] for e in data['entries']], ['bad thing'])
        self.assertIn('level', data['state'])
        rv = self.client.get('/api/logs/download')
        self.assertIn('attachment', rv.headers['Content-Disposition'])
        text = rv.get_data(as_text=True)
        self.assertIn('hello', text)
        self.assertIn('ERROR', text)
        self.assertEqual(self.client.get('/api/logs?after=x').status_code, 400)

    def test_log_viewers_restricts_access(self):
        self.config.set('webinterface', 'log_viewers', 'Admin@Example.com, user:ops')
        self.assertEqual(self.client.get('/api/logs').status_code, 403)
        self.assertEqual(self.client.get('/api/logs', headers={EMAIL_HEADER: 'eve@example.com'}).status_code, 403)
        self.assertEqual(self.client.get('/api/logs', headers={EMAIL_HEADER: 'admin@example.com'}).status_code, 200)
        me = self.client.get('/api/me', headers={EMAIL_HEADER: 'eve@example.com'}).get_json()
        self.assertFalse(me['can_view_logs'])
        self.config.set('webinterface', 'log_viewers', '')
        self.assertTrue(self.client.get('/api/me').get_json()['can_view_logs'])

    def test_client_error_report_is_logged_and_throttled(self):
        import web_logs
        captured = []
        handler = logging.Handler()
        handler.emit = captured.append
        client_log = logging.getLogger('bot.webui')
        client_log.addHandler(handler)
        web_logs._client_window[:] = [0.0, 0]
        try:
            rv = self.client.post('/api/logs/client', json={'message': 'TypeError: x is undefined',
                                                            'stack': 'at foo', 'url': '/#lists'})
            self.assertEqual(rv.status_code, 200)
            self.assertEqual(self.client.post('/api/logs/client', json={}).status_code, 400)
            for _ in range(web_logs.CLIENT_REPORTS_PER_MINUTE):
                rv = self.client.post('/api/logs/client', json={'message': 'spam'})
            self.assertEqual(rv.status_code, 429)
        finally:
            client_log.removeHandler(handler)
            web_logs._client_window[:] = [0.0, 0]
        self.assertIn('TypeError: x is undefined', captured[0].getMessage())


if __name__ == '__main__':
    unittest.main()
