import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import web_search  # noqa: E402


BILI_FIXTURE = {
    "code": 0,
    "data": {
        "result": [
            {"type": "video", "bvid": "BV1xx411c7mD",
             "title": "【<em class=\"keyword\">洛天依</em>】千年食谱颂",
             "author": "H.K.君", "duration": "3:45",
             "pic": "//i1.hdslb.com/bfs/archive/abc.jpg"},
            {"type": "media_bangumi", "bvid": "", "title": "should be skipped"},
            {"type": "video", "bvid": "BV1yy411c7mE",
             "title": "plain title", "author": "someone",
             "duration": "1:02:03", "pic": "https://i2.hdslb.com/x.png"},
        ]
    },
}

YT_FIXTURE = {
    "entries": [
        {"id": "dQw4w9WgXcQ", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
         "title": "A Video", "uploader": "Channel A", "duration": 212,
         "thumbnails": [{"url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hq.jpg"}]},
        None,  # yt-dlp emits None for failed flat entries
        {"id": "abc", "title": "No url field", "duration": None,
         "channel": "Channel B"},
    ]
}


class BiliParserTestCase(unittest.TestCase):
    def test_parses_and_normalizes(self):
        results = web_search.parse_bilibili_results(BILI_FIXTURE)
        self.assertEqual(2, len(results))
        first = results[0]
        self.assertEqual('bilibili', first['source'])
        self.assertEqual('BV1xx411c7mD', first['id'])
        self.assertEqual('【洛天依】千年食谱颂', first['title'])  # em tags stripped
        self.assertEqual(225, first['duration'])
        self.assertEqual('https://i1.hdslb.com/bfs/archive/abc.jpg', first['thumbnail'])
        self.assertEqual('https://www.bilibili.com/video/BV1xx411c7mD', first['url'])
        self.assertEqual(3723, results[1]['duration'])

    def test_error_code_yields_empty(self):
        self.assertEqual([], web_search.parse_bilibili_results({"code": -412}))
        self.assertEqual([], web_search.parse_bilibili_results(None))

    def test_limit(self):
        results = web_search.parse_bilibili_results(BILI_FIXTURE, limit=1)
        self.assertEqual(1, len(results))


class YoutubeParserTestCase(unittest.TestCase):
    def test_parses_entries(self):
        results = web_search.parse_youtube_entries(YT_FIXTURE)
        self.assertEqual(2, len(results))
        self.assertEqual('youtube', results[0]['source'])
        self.assertEqual(212, results[0]['duration'])
        self.assertEqual('https://i.ytimg.com/vi/dQw4w9WgXcQ/hq.jpg',
                         results[0]['thumbnail'])
        # entry without url falls back to watch?v=<id>, channel as uploader
        self.assertEqual('https://www.youtube.com/watch?v=abc', results[1]['url'])
        self.assertEqual('Channel B', results[1]['uploader'])
        self.assertEqual(0, results[1]['duration'])

    def test_empty_info(self):
        self.assertEqual([], web_search.parse_youtube_entries(None))
        self.assertEqual([], web_search.parse_youtube_entries({}))


class UnifiedSearchTestCase(unittest.TestCase):
    def test_interleaves_sources(self):
        yt = [{'source': 'youtube', 'id': str(i)} for i in range(3)]
        bl = [{'source': 'bilibili', 'id': str(i)} for i in range(2)]
        with mock.patch.object(web_search, 'search_youtube', return_value=yt), \
                mock.patch.object(web_search, 'search_bilibili', return_value=bl):
            results, failed = web_search.unified_search('query')
        self.assertEqual([], failed)
        self.assertEqual(['youtube', 'bilibili', 'youtube', 'bilibili', 'youtube'],
                         [r['source'] for r in results])

    def test_failed_source_degrades(self):
        yt = [{'source': 'youtube', 'id': '1'}]
        with mock.patch.object(web_search, 'search_youtube', return_value=yt), \
                mock.patch.object(web_search, 'search_bilibili',
                                  side_effect=RuntimeError('412')):
            results, failed = web_search.unified_search('query')
        self.assertEqual(['bilibili'], failed)
        self.assertEqual(1, len(results))


if __name__ == '__main__':
    unittest.main()
