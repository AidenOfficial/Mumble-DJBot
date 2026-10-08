"""分片上传:断点续传、幂等重试、大小/类型/路径校验、视频抽音轨后登记曲库。"""
import configparser
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import variables as var  # noqa: E402
import web_upload  # noqa: E402
from database import Condition, DatabaseMigration, MusicDatabase, SettingsDatabase  # noqa: E402

HAS_FFMPEG = shutil.which('ffmpeg') is not None


class UploadTest(unittest.TestCase):
    def setUp(self):
        from flask import Flask
        self.root = tempfile.TemporaryDirectory()
        base = self.root.name
        self._saved = {n: getattr(var, n, None) for n in ('config', 'db', 'music_db', 'tmp_folder', 'music_folder')}
        var.tmp_folder = os.path.join(base, 'cache') + os.sep
        var.music_folder = os.path.join(base, 'music') + os.sep
        os.makedirs(var.tmp_folder)
        os.makedirs(var.music_folder)
        config = configparser.ConfigParser(interpolation=None)
        config.read_dict({'webinterface': {'upload_enabled': 'True', 'max_upload_file_size': '50M',
                                           'upload_extract_audio': 'True'}})
        var.config = config
        var.db = SettingsDatabase(os.path.join(base, 's.db'))
        var.music_db = MusicDatabase(os.path.join(base, 'm.db'))
        DatabaseMigration(var.db, var.music_db).migrate()
        app = Flask(__name__)
        app.register_blueprint(web_upload.create_blueprint(lambda f: f))
        self.client = app.test_client()
        self._chunk = web_upload.CHUNK_SIZE

    def tearDown(self):
        web_upload.CHUNK_SIZE = self._chunk
        for n, v in self._saved.items():
            setattr(var, n, v)
        self.root.cleanup()

    def init(self, filename, size, **extra):
        return self.client.post('/api/upload/init', json={'filename': filename, 'size': size, **extra})

    def put(self, uid, offset, data):
        return self.client.put(f'/api/upload/{uid}?offset={offset}', data=data,
                               content_type='application/octet-stream')

    def wait_done(self, uid, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            meta = self.client.get(f'/api/upload/{uid}').get_json()
            if meta['status'] in ('done', 'error'):
                return meta
            time.sleep(0.05)
        self.fail('upload never finished')

    def make_media(self, name, video):
        path = os.path.join(self.root.name, name)
        cmd = ['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=3']
        if video:
            cmd += ['-f', 'lavfi', '-i', 'color=c=blue:s=320x240:d=3', '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest']
        cmd.append(path)
        subprocess.run(cmd, check=True)
        with open(path, 'rb') as f:
            return f.read()

    def upload_bytes(self, filename, data, chunk=1024 * 64):
        web_upload.CHUNK_SIZE = chunk
        rv = self.init(filename, len(data))
        self.assertEqual(rv.status_code, 200, rv.get_json())
        uid = rv.get_json()['upload_id']
        for off in range(0, len(data), chunk):
            self.assertEqual(self.put(uid, off, data[off:off + chunk]).status_code, 200)
        return uid

    @unittest.skipUnless(HAS_FFMPEG, 'ffmpeg missing')
    def test_video_upload_extracts_audio_and_registers(self):
        data = self.make_media('Live 演唱会.mp4', video=True)
        uid = self.upload_bytes('Live 演唱会.mp4', data)
        self.assertEqual(self.client.post(f'/api/upload/{uid}/finish').status_code, 200)
        meta = self.wait_done(uid)
        self.assertEqual(meta['status'], 'done', meta)
        self.assertTrue(meta['extracted'])
        self.assertEqual(meta['path'], os.path.join('uploads', 'Live 演唱会.mka'))
        self.assertLess(meta['final_size'], len(data))
        records = var.music_db.query_music(Condition().and_equal('type', 'file'))
        self.assertEqual(records[0]['id'], meta['item_id'])
        # 暂存文件已清理
        self.assertFalse(os.path.exists(web_upload._data_path(uid)))

    @unittest.skipUnless(HAS_FFMPEG, 'ffmpeg missing')
    def test_audio_upload_kept_as_is_with_unique_name(self):
        data = self.make_media('song.mp3', video=False)
        os.makedirs(os.path.join(var.music_folder, 'uploads'))
        open(os.path.join(var.music_folder, 'uploads', 'song.mp3'), 'wb').close()
        uid = self.upload_bytes('song.mp3', data)
        self.client.post(f'/api/upload/{uid}/finish')
        meta = self.wait_done(uid)
        self.assertEqual(meta['path'], os.path.join('uploads', 'song (2).mp3'))
        self.assertFalse(meta['extracted'])

    def test_resume_and_idempotent_retry(self):
        web_upload.CHUNK_SIZE = 10
        uid = self.init('a.mp3', 25).get_json()['upload_id']
        self.assertEqual(self.put(uid, 0, b'a' * 10).get_json()['received'], 10)
        # 同一片重发:确认但不重复写
        self.assertEqual(self.put(uid, 0, b'a' * 10).get_json()['received'], 10)
        # 跳着发:409 并告诉客户端从哪里续
        rv = self.put(uid, 20, b'c' * 5)
        self.assertEqual(rv.status_code, 409)
        self.assertEqual(rv.get_json()['received'], 10)
        self.assertEqual(self.put(uid, 10, b'b' * 10).status_code, 200)
        # 没传完不能 finish
        self.assertEqual(self.client.post(f'/api/upload/{uid}/finish').status_code, 409)
        # 超过声明大小
        self.assertEqual(self.put(uid, 20, b'c' * 6).status_code, 413)
        self.assertEqual(self.put(uid, 20, b'c' * 5).status_code, 200)
        with open(web_upload._data_path(uid), 'rb') as f:
            self.assertEqual(f.read(), b'a' * 10 + b'b' * 10 + b'c' * 5)

    def test_validation(self):
        self.assertEqual(self.init('x.exe', 10).status_code, 415)
        self.assertEqual(self.init('x.mp3', 60 * 1024 * 1024).status_code, 413)
        self.assertEqual(self.init('x.mp3', 0).status_code, 400)
        self.assertEqual(self.init('x.mp3', 10, targetdir='../../etc').status_code, 403)
        self.assertEqual(self.client.get('/api/upload/../../x').status_code, 404)
        var.config.set('webinterface', 'upload_enabled', 'False')
        self.assertEqual(self.init('x.mp3', 10).status_code, 403)

    def test_non_media_content_rejected_at_finish(self):
        data = b'#!/bin/sh\necho pwned\n' * 10
        uid = self.upload_bytes('evil.mp3', data)
        rv = self.client.post(f'/api/upload/{uid}/finish')
        self.assertEqual(rv.status_code, 415)
        self.assertFalse(os.path.exists(web_upload._data_path(uid)))

    def test_clean_filename_keeps_unicode(self):
        self.assertEqual(web_upload.clean_filename('../../夜に駆ける.mp3'), '夜に駆ける.mp3')
        self.assertEqual(web_upload.clean_filename('C:\\x\\a<b>.mp3'), 'ab.mp3')
        self.assertEqual(web_upload.clean_filename('.hidden.mp3'), 'hidden.mp3')

    def test_stale_uploads_pruned(self):
        uid = self.init('a.mp3', 5).get_json()['upload_id']
        old = time.time() - 2 * web_upload.STALE_SECONDS
        for p in (web_upload._data_path(uid), web_upload._meta_path(uid)):
            os.utime(p, (old, old))
        web_upload.prune_stale()
        self.assertEqual(self.client.get(f'/api/upload/{uid}').status_code, 404)


if __name__ == '__main__':
    unittest.main()
