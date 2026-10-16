import configparser
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import variables as var  # noqa: E402
from media import artwork, recognize  # noqa: E402
from media.artwork import Candidate  # noqa: E402


def song(title, artists, duration, album='Album', image='http://img/x.jpg'):
    return {'title': title, 'artists': list(artists), 'album': album,
            'duration': duration, 'image': image, 'source': 'fake'}


class ParseTitleTest(unittest.TestCase):
    def tracks(self, title):
        return [c.track for c in artwork.parse_title(title)]

    def test_book_title_marks(self):
        title = '【QQ飞车｜回忆杀】百万级录音棚听《Loves Me Not》QQ飞车登陆BGM【Hi-Res】'
        self.assertEqual(['Loves Me Not'], self.tracks(title))

    def test_japanese_brackets_with_artist_before(self):
        cands = artwork.parse_title('米津玄師 MV「Lemon」')
        self.assertEqual(Candidate('Lemon'), cands[0])
        self.assertIn(Candidate('Lemon', '米津玄師'), cands)

    def test_curly_quotes(self):
        self.assertEqual('夜に駆ける', self.tracks('【中日字幕】“夜に駆ける”YOASOBI')[0])

    def test_artist_dash_title(self):
        cands = artwork.parse_title('Rick Astley - Never Gonna Give You Up (Official Music Video)')
        self.assertEqual(Candidate('Never Gonna Give You Up', 'Rick Astley'), cands[0])

    def test_title_slash_artist(self):
        cands = artwork.parse_title('ヴァンパイア / DECO*27 feat. 初音ミク')
        self.assertEqual('ヴァンパイア', cands[0].track)

    def test_space_separated_cjk(self):
        cands = artwork.parse_title('周杰伦 晴天 高音质')
        self.assertIn(Candidate('晴天', '周杰伦'), cands)

    def test_plain_title(self):
        self.assertEqual(['Bohemian Rhapsody'], self.tracks('Bohemian Rhapsody [HD]'))

    def test_skip_covers_and_compilations(self):
        self.assertTrue(artwork.should_skip('【翻唱】Lemon 米津玄师'))
        self.assertTrue(artwork.should_skip('Lemon (piano cover)'))
        self.assertTrue(artwork.should_skip('YOASOBI 武道館 LIVE'))
        self.assertTrue(artwork.should_skip('Some song', duration=3600))
        self.assertFalse(artwork.should_skip('百万级录音棚听《Loves Me Not》', duration=180))


class ChooseMatchTest(unittest.TestCase):
    def choose(self, results, cand, title, duration=0, extra=''):
        hay = artwork.words(title + ' ' + extra)
        return artwork.choose_match(results, cand, hay, duration, title)

    def test_title_must_match_exactly(self):
        # "Loves Me Not" 不能配成 "Love Me Not"
        results = [song('Love Me Not', ['Ravyn Lenae'], 213)]
        self.assertIsNone(self.choose(results, Candidate('Loves Me Not'), 'x《Loves Me Not》', 213))

    def test_same_title_different_artists_without_evidence_is_rejected(self):
        results = [song('Loves Me Not', ['t.A.T.u.'], 175), song('Loves Me Not', ['Lexi Faye'], 174.5)]
        # 两位歌手时长都在误差内:分不清,宁可用视频封面
        self.assertIsNone(self.choose(results, Candidate('Loves Me Not'), '《Loves Me Not》', 175.5))
        # 视频比两首都长得多:也不能确定
        self.assertIsNone(self.choose(results, Candidate('Loves Me Not'), '《Loves Me Not》', 230))

    def test_unique_duration_match_is_accepted(self):
        results = [song('Loves Me Not', ['t.A.T.u.'], 174.7), song('Loves Me Not', ['Kate Grahn'], 203)]
        m = self.choose(results, Candidate('Loves Me Not'), '《Loves Me Not》', 176)
        self.assertEqual(['t.A.T.u.'], m['artists'])

    def test_artist_in_tags_is_evidence(self):
        results = [song('Loves Me Not', ['Kate Grahn'], 203), song('Loves Me Not', ['t.A.T.u.'], 175)]
        m = self.choose(results, Candidate('Loves Me Not'), '《Loves Me Not》', 240, extra='QQ飞车 t.A.T.u.')
        self.assertEqual(['t.A.T.u.'], m['artists'])

    def test_traditional_simplified_artist(self):
        results = [song('江南', ['林俊傑'], 268)]
        m = self.choose(results, Candidate('江南', '林俊杰'), '林俊杰 - 江南 (官方MV)', 300)
        self.assertIsNotNone(m)

    def test_song_title_is_not_artist_evidence(self):
        # 乐队叫 Lemon 唱的歌也叫 Lemon:标题里的 "Lemon" 不能算歌手出现过
        results = [song('Lemon', ['Lemon'], 150)]
        self.assertIsNone(self.choose(results, Candidate('Lemon'), '「Lemon」', 256))

    def test_cover_versions_in_catalog_are_rejected(self):
        results = [song('晴天 (原唱 周杰伦)', ['RyaVocal'], 270.7),
                   song('晴天 (钢琴版) [原唱: 周杰伦]', ['纪钧瀚'], 269.5)]
        self.assertIsNone(self.choose(results, Candidate('晴天', '周杰伦'), '周杰伦 晴天', 270))

    def test_variant_allowed_when_video_is_that_variant(self):
        results = [song('Song (Remix)', ['DJ'], 200)]
        m = self.choose(results, Candidate('Song'), 'DJ - Song (Remix)', 200)
        self.assertIsNotNone(m)

    def test_prefers_plain_title_with_album(self):
        results = [song('Never Gonna Give You Up (Cake Mix)', ['Rick Astley'], 347),
                   song('Never Gonna Give You Up', ['Rick Astley'], 213, album=''),
                   song('Never Gonna Give You Up', ['Rick Astley'], 214, album='Whenever You Need Somebody')]
        m = self.choose(results, Candidate('Never Gonna Give You Up', 'Rick Astley'),
                        'Rick Astley - Never Gonna Give You Up (Official Music Video)', 213)
        self.assertEqual('Whenever You Need Somebody', m['album'])


class RegressionTest(unittest.TestCase):
    """实机日志里出现过的误匹配 / 漏匹配。"""

    def choose(self, results, title, duration):
        cand = artwork.parse_title(title)[0]
        return artwork.choose_match(results, cand, artwork.words(title), duration, title)

    def test_dash_without_space_on_one_side(self):
        self.assertEqual(Candidate('Loves me not', 't.A.T.u.'), artwork.parse_title('t.A.T.u.- Loves me not lyrics')[0])
        self.assertEqual('A-ha', artwork.parse_title('A-ha')[0].track)

    def test_parentheses_that_are_part_of_the_title(self):
        m = self.choose([song('Right Now (Na Na Na)', ['Akon'], 241)], 'Akon - Right Now Na Na Na (Lyrics)', 242)
        self.assertIsNotNone(m)

    def test_taylors_version_preferred_over_original(self):
        results = [song('I Knew You Were Trouble.', ['Taylor Swift'], 219, album='Red'),
                   song("I Knew You Were Trouble (Taylor's Version)", ['Taylor Swift'], 219, album="Red (Taylor's Version)")]
        m = self.choose(results, "Taylor Swift - I Knew You Were Trouble (Taylor's Version) (Lyric Video)", 221)
        self.assertEqual("Red (Taylor's Version)", m['album'])

    def test_karaoke_label_rejected(self):
        results = [song('Baby (Feat. Ludacris) (In The Style Of Justin Bieber)', ['Karaoke - Ameritz'], 225)]
        self.assertIsNone(self.choose(results, 'Justin Bieber - Baby ft. Ludacris', 225))

    def test_named_artist_conflict_rejected_even_with_same_duration(self):
        for title, result in [
            ("Charlie Puth - We Don't Talk Anymore (feat. Selena Gomez) [Official Video]",
             song("We Don't Talk Anymore", ['Pablo Cepeda'], 218)),
            ('One Direction - Best Song Ever (Audio)', song('Best Song Ever', ['Party all night'], 200)),
            ('Shingeki no Kyojin S1 OP1 | Linked Horizon - Guren no Yumiya',
             song('Guren No Yumiya (From "Attack on Titan")', ['PelleK'], 300)),
        ]:
            self.assertIsNone(self.choose([result], title, result['duration']), title)

    def test_localized_artist_still_matches_by_duration(self):
        m = self.choose([song('晴天', ['Jay Chou'], 269.7)], '周杰伦 晴天 高音质', 270)
        self.assertIsNotNone(m)

    def test_rerecorded_versions_rejected(self):
        for t in ('I Want It That Way (Reimagined)', 'Let Her Go (Recorded at Deezer, Sao Paulo)'):
            self.assertTrue(artwork.is_variant({'title': t}, 'Some Video'), t)
        self.assertFalse(artwork.is_variant({'title': 'Lean On', 'artists': ['Major Lazer', 'DJ Snake']},
                                            'Major Lazer - Lean On'))

    def test_party_compilation_detected(self):
        self.assertTrue(artwork.is_compilation({'album': 'High School Party 2020'}))
        self.assertFalse(artwork.is_compilation({'album': 'My World 2.0'}))

    def test_query_language(self):
        self.assertEqual('en', artwork._language("Bon Jovi It's My Life"))
        self.assertEqual('ja', artwork._language('アイドル'))
        self.assertEqual('zh', artwork._language('晴天'))
        self.assertEqual('ko', artwork._language('불면증'))


class FindAlbumTest(unittest.TestCase):
    def setUp(self):
        self._config = var.config
        var.config = None
        self.queries = []

    def tearDown(self):
        var.config = self._config

    def provider(self, results):
        def search(q):
            self.queries.append(q)
            return results
        return search

    def test_skipped_video_does_not_query(self):
        m = artwork.find_album({'title': '【翻唱】Lemon', 'duration': 250}, [self.provider([])])
        self.assertIsNone(m)
        self.assertEqual([], self.queries)

    def test_platform_metadata_wins(self):
        results = [song('Lemon', ['米津玄師'], 256)]
        m = artwork.find_album({'title': 'whatever video title', 'duration': 300,
                                'track': 'Lemon', 'artist': '米津玄師'}, [self.provider(results)])
        self.assertEqual(['米津玄師'], m['artists'])

    def test_compilation_only_used_as_fallback(self):
        compilation = song('Song', ['Artist'], 200, album='Greatest Hits Vol. 3', image='http://img/comp.jpg')
        original = song('Song', ['Artist'], 200, album='Original', image='http://img/orig.jpg')
        m = artwork.find_album({'title': 'Artist - Song', 'duration': 200},
                               [self.provider([compilation]), self.provider([original])])
        self.assertEqual('http://img/orig.jpg', m['image'])
        m = artwork.find_album({'title': 'Artist - Song', 'duration': 200}, [self.provider([compilation])])
        self.assertEqual('http://img/comp.jpg', m['image'])

    def test_call_budget(self):
        artwork.find_album({'title': 'A - B / C 「D」', 'duration': 200}, [self.provider([])] * 5)
        self.assertLessEqual(len(self.queries), artwork.MAX_CALLS_PER_ITEM)


def card(title, artist, album=''):
    return {'title': title, 'artist': artist, 'album': album}


class MusicCandidateTest(unittest.TestCase):
    """YouTube「Music」卡片的校验,用例都是实测到的卡片。"""
    def pick(self, cards, title, extra=''):
        return artwork.music_candidate(cards, title, artwork.words(title + ' ' + extra))

    def test_matching_card_is_used(self):
        self.assertEqual(Candidate('Lemon', 'Kenshi Yonezu'),
                         self.pick([card('Lemon', 'Kenshi Yonezu', 'Lemon')], '米津玄師  Kenshi Yonezu  - Lemon'))

    def test_translated_title_trusts_card(self):
        # 英文标题,卡片给的是原名
        c = self.pick([card('打上花火', 'DAOKO×米津玄師')], 'DAOKO × Kenshi Yonezu “Fireworks” MUSIC VIDEO')
        self.assertEqual(Candidate('打上花火', 'DAOKO×米津玄師'), c)

    def test_version_label_from_card_dropped(self):
        c = self.pick([card('Perfect Duet (with Beyoncé)', 'Ed Sheeran')], 'Ed Sheeran - Perfect (Official Music Video)')
        self.assertEqual('Perfect', c.track)
        c = self.pick([card('踊り子 (DOME TOUR 2026 "SILENCE")', 'Vaundy')],
                      '【第75回NHK紅白歌合戦 歌唱曲】踊り子 / Vaundy：MUSIC VIDEO')
        self.assertEqual('踊り子', c.track)

    def test_card_shorter_than_title_guess(self):
        c = self.pick([card('Pale Blue', 'Kenshi Yonezu')], '米津玄師  - Pale Blue / Kenshi Yonezu')
        self.assertEqual(Candidate('Pale Blue', 'Kenshi Yonezu'), c)

    def test_wrong_song_rejected(self):
        # Content ID 配成了别人的同名歌
        self.assertIsNone(self.pick([card('O Amante', 'Thiago Matheus', 'Fénix')], 'El Amante - Nicky Jam | Video Oficial'))
        # 歌手对得上,但标题里写的是另一首
        self.assertIsNone(self.pick([card('Shape of You', 'Ed Sheeran')], 'Ed Sheeran - Perfect (Official Music Video)'))

    def test_several_cards_rejected(self):
        cards = [card('Shape of You', 'Ed Sheeran'), card('Night Changes', 'One Direction')]
        self.assertIsNone(self.pick(cards, 'Señorita - Shawn Mendes [Lyrics] | Ed Sheeran, One Direction'))

    def test_card_mirroring_video_title_rejected(self):
        self.assertIsNone(self.pick([card('YOASOBI - 群青  / THE FIRST TAKE', 'YOASOBI')],
                                    'YOASOBI - Gunjo / THE FIRST TAKE'))
        self.assertIsNone(self.pick([card('Alan Walker - Faded', '')], 'Alan Walker - Faded'))

    def test_artist_from_uploader_counts(self):
        c = self.pick([card('Pretender', 'Official髭男dism')], 'OFFICIAL HIGE DANDISM - Pretender［Official Video］',
                      extra='Official髭男dism')
        self.assertEqual(Candidate('Pretender', 'Official髭男dism'), c)
        self.assertIsNone(self.pick([card('Pretender', 'Official髭男dism')],
                                    'OFFICIAL HIGE DANDISM - Pretender［Official Video］'))


# next 接口返回里「Music」区块的实际结构(删掉了无关字段)
NEXT_RESPONSE = {'engagementPanels': [
    {'engagementPanelSectionListRenderer': {'content': {'sectionListRenderer': {}}}},
    {'engagementPanelSectionListRenderer': {'content': {'structuredDescriptionContentRenderer': {'items': [
        {'videoDescriptionHeaderRenderer': {}},
        {'horizontalCardListRenderer': {
            'cards': [{'videoAttributeViewModel': {'title': 'Other', 'subtitle': 'x'}}],
            'header': {'richListHeaderRenderer': {'title': {'simpleText': 'People mentioned'}}},
            'footerButton': {'buttonViewModel': {'iconName': 'PERSON'}}}},
        {'horizontalCardListRenderer': {
            'cards': [{'videoAttributeViewModel': {
                'title': 'Lemon', 'subtitle': 'Kenshi Yonezu', 'secondarySubtitle': {'content': 'Lemon'}}}],
            'header': {'richListHeaderRenderer': {'title': {'simpleText': '音乐'},
                                                  'subtitle': {'simpleText': '1 song'}}},
            'footerButton': {'buttonViewModel': {'iconName': 'MUSIC'}}}},
    ]}}}},
]}

# Shazam 返回的实际结构(删掉了无关字段)
SHAZAM_RESPONSE = {
    'matches': [{'id': '1', 'offset': 61.2}],
    'track': {
        'title': 'Lemon', 'subtitle': '米津玄师',
        'images': {'coverart': 'https://is1-ssl.mzstatic.com/image/thumb/Music124/v4/x/dj.jpg/400x400cc.jpg',
                   'coverarthq': 'https://is1-ssl.mzstatic.com/image/thumb/Music124/v4/x/dj.jpg/400x400cc.jpg'},
        'sections': [{'type': 'SONG', 'metadata': [{'title': '专辑', 'text': 'STRAY SHEEP'},
                                                    {'title': '唱片公司', 'text': 'Sony Music Labels Inc.'}]},
                     {'type': 'RELATED'}],
    },
}


class RecognizeParseTest(unittest.TestCase):
    def test_music_section(self):
        self.assertEqual([card('Lemon', 'Kenshi Yonezu', 'Lemon')], recognize.parse_music_section(NEXT_RESPONSE))
        self.assertEqual([], recognize.parse_music_section({}))
        self.assertEqual([], recognize.parse_music_section(None))

    def test_not_youtube_skips_request(self):
        self.assertEqual([], recognize.youtube_music('https://www.bilibili.com/video/BV1xx411c7mD'))

    def test_shazam_response(self):
        m = recognize.parse_shazam(SHAZAM_RESPONSE)
        self.assertEqual(('Lemon', ['米津玄师'], 'STRAY SHEEP', 'shazam'),
                         (m['title'], m['artists'], m['album'], m['source']))
        self.assertTrue(m['image'].endswith('/1000x1000cc.jpg'))
        self.assertIsNone(recognize.parse_shazam({'matches': [], 'tagid': 'x'}))

    def test_clip_offsets(self):
        self.assertEqual([70.0, 130.0], recognize.clip_offsets(200))
        # 35% 处落在要跳过的片段里:挪到最近的音乐部分(片段前面那段)
        self.assertEqual([48.0, 130.0], recognize.clip_offsets(200, [(60, 90)]))
        self.assertEqual([90.0, 130.0], recognize.clip_offsets(200, [(40, 90)]))
        # 整首都是要跳过的片段:不认
        self.assertEqual([], recognize.clip_offsets(200, [(0, 200)]))
        self.assertEqual([0.0], recognize.clip_offsets(8))
        self.assertEqual([30.0], recognize.clip_offsets(0))
        # 剩下的音乐不够两段:只截一段
        self.assertEqual([100.0], recognize.clip_offsets(200, [(0, 100), (112, 200)]))


class FakeURLItem:
    type = 'url'

    def __init__(self):
        self.id = 'a' * 32
        self.title = '米津玄師 MV「Lemon」'
        self.duration = 260
        self.url = 'https://www.youtube.com/watch?v=SX_ViT4Ra7k'
        self.thumbnail = ''
        self.artwork = ''
        self.artist = ''
        self.album = ''
        self.track = None
        self.shazam = ''
        self.ready = 'validated'
        self.downloading = False
        self.path = ''
        self.version = 0

    def format_title(self):
        return self.title


class ResolveTest(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        self.tmp = tempfile.TemporaryDirectory()
        self._saved = (var.config, var.tmp_folder)
        config = configparser.ConfigParser()
        config.add_section('bot')
        config.set('bot', 'artwork_folder', self.tmp.name)
        var.config = config
        var.tmp_folder = self.tmp.name + os.sep
        self.fetched = []
        self._fetch = artwork._fetch_image

        def fake_fetch(url):
            self.fetched.append(url)
            if 'broken' in url:
                raise IOError('404')
            return Image.new('RGB', (1280, 720) if 'ytimg' in url or 'video' in url else (1000, 1000), 'red')
        artwork._fetch_image = fake_fetch
        self.extracted = []
        self._extract = artwork._extract_info

        def fake_extract(url):
            self.extracted.append(url)
            return {}
        artwork._extract_info = fake_extract
        # 「Music」卡片和 Shazam 都不联网
        self.cards, self.card_lookups = [], []
        self.heard, self.shazam_calls = None, []
        self._recognize = (recognize.youtube_music, recognize.shazam)

        def fake_cards(url):
            self.card_lookups.append(url)
            return self.cards
        recognize.youtube_music = fake_cards

        def fake_shazam(path, duration=0, url=''):
            self.shazam_calls.append(path)
            return self.heard
        recognize.shazam = fake_shazam

    def tearDown(self):
        artwork._fetch_image = self._fetch
        artwork._extract_info = self._extract
        recognize.youtube_music, recognize.shazam = self._recognize
        var.config, var.tmp_folder = self._saved
        self.tmp.cleanup()

    def test_album_art_found(self):
        item = FakeURLItem()
        provider = lambda q: [song('Lemon', ['米津玄師'], 256, album='Lemon', image='http://img/album.jpg')]  # noqa
        artwork.resolve(item, {'title': item.title, 'duration': 260, 'thumbnail': 'http://video.jpg'},
                        providers=[provider])
        self.assertEqual('album', item.artwork)
        self.assertEqual('米津玄師', item.artist)
        self.assertEqual('Lemon', item.album)
        self.assertEqual('Lemon', item.track)
        self.assertEqual(['http://img/album.jpg'], self.fetched)
        self.assertTrue(artwork.has_hd(item.id))
        self.assertTrue(item.thumbnail)
        self.assertGreater(item.version, 0)
        from PIL import Image
        with Image.open(artwork.path_for(item.id)) as im:
            self.assertEqual((1000, 1000), im.size)

    def test_falls_back_to_best_video_thumbnail(self):
        item = FakeURLItem()
        info = {'title': item.title, 'duration': 260, 'thumbnail': 'http://broken.jpg',
                'thumbnails': [{'url': 'http://video-small.jpg', 'width': 120, 'height': 90},
                               {'url': 'http://video-big.jpg', 'width': 1280, 'height': 720}]}
        artwork.resolve(item, info, providers=[lambda q: []])
        self.assertEqual('video', item.artwork)
        self.assertEqual(['http://broken.jpg', 'http://video-big.jpg'], self.fetched)
        self.assertEqual('', item.artist)

    def test_lookup_disabled_uses_video_cover(self):
        var.config.set('bot', 'artwork_lookup', 'False')
        item = FakeURLItem()
        called = []
        artwork.resolve(item, {'title': item.title, 'thumbnail': 'http://video.jpg'},
                        providers=[lambda q: called.append(q) or []])
        self.assertEqual([], called)
        self.assertEqual('video', item.artwork)

    def test_nothing_available_marks_none(self):
        item = FakeURLItem()
        item.url = 'https://example.com/a'
        artwork.resolve(item, None, providers=[lambda q: []])
        self.assertEqual('none', item.artwork)
        self.assertTrue(item.artwork_done)
        # 不再重复排队
        artwork.ensure(item)
        self.assertNotIn(item.id, artwork._queued)

    def test_restored_item_reads_info_for_evidence(self):
        # 从上次队列恢复的条目没有 yt-dlp 信息:读一次拿 UP 主 / 标签
        item = FakeURLItem()
        artwork.resolve(item, None, providers=[lambda q: []])
        self.assertEqual([item.url], self.extracted)

    def test_video_cover_never_overwrites_album_cover(self):
        item = FakeURLItem()
        provider = lambda q: [song('Lemon', ['米津玄師'], 256, album='Lemon', image='http://img/album.jpg')]  # noqa
        artwork.resolve(item, {'title': item.title, 'uploader': 'x', 'duration': 260}, providers=[provider])
        self.assertEqual('album', item.artwork)
        # 抢先下载视频封面的线程晚到了
        self.assertFalse(artwork._video_step(item, {'thumbnail': 'http://video.jpg'}))
        self.assertEqual('album', item.artwork)

    def test_video_first_then_album_upgrade(self):
        item = FakeURLItem()
        self.assertTrue(artwork._video_step(item, {'thumbnail': 'http://video.jpg'}))
        self.assertEqual('video', item.artwork)
        self.assertFalse(getattr(item, 'artwork_done', False))
        provider = lambda q: [song('Lemon', ['米津玄師'], 256, album='Lemon', image='http://img/album.jpg')]  # noqa
        artwork.resolve(item, {'title': item.title, 'uploader': 'x', 'duration': 260}, providers=[provider])
        self.assertEqual('album', item.artwork)
        self.assertTrue(item.artwork_done)

    def test_ensure_skips_finished_items(self):
        item = FakeURLItem()
        item.artwork, item.artwork_done = 'none', True
        artwork.ensure(item, urgent=True)
        self.assertNotIn(item.id, artwork._queued)
        # 上一版存的 'video'(没有 artwork_done)要按新规则再查一次
        item.artwork, item.artwork_done = 'video', False
        with open(artwork.path_for(item.id), 'wb') as f:
            f.write(b'x')
        artwork.ensure(item)
        self.assertIn(item.id, artwork._queued)
        with artwork._lock:
            artwork._queued.discard(item.id)

    def test_old_album_item_without_track_is_looked_up_again(self):
        # 上一版的专辑封面条目没存正式歌名:再查一次补上
        item = FakeURLItem()
        item.artwork, item.artwork_done, item.track = 'album', True, None
        with open(artwork.path_for(item.id), 'wb') as f:
            f.write(b'x')
        artwork.ensure(item)
        self.assertIn(item.id, artwork._queued)
        with artwork._lock:
            artwork._queued.discard(item.id)
        # 这次没查到也记下来,不会每次显示都重查
        artwork.resolve(item, {'title': item.title, 'uploader': 'x'}, providers=[lambda q: []])
        self.assertEqual('', item.track)
        artwork.ensure(item)
        self.assertNotIn(item.id, artwork._queued)

    def audio(self, item):
        item.path = os.path.join(self.tmp.name, item.id)
        with open(item.path, 'wb') as f:
            f.write(b'audio')
        item.ready = 'yes'

    def test_music_card_drives_lookup(self):
        item = FakeURLItem()
        item.title = 'DAOKO × Kenshi Yonezu “Fireworks” MUSIC VIDEO'
        self.cards = [card('打上花火', 'DAOKO×米津玄師', '打上花火')]
        queries = []

        def provider(q):
            queries.append(q)
            return [song('打上花火', ['DAOKO', '米津玄師'], 290)] if '打上花火' in q else []
        artwork.resolve(item, {'title': item.title, 'uploader': 'DAOKO', 'duration': 290}, providers=[provider])
        self.assertEqual('打上花火', queries[0])
        self.assertEqual(('album', '打上花火', 'DAOKO, 米津玄師'), (item.artwork, item.track, item.artist))
        self.assertEqual('', item.shazam)

    def test_music_card_name_kept_without_catalog_match(self):
        item = FakeURLItem()
        item.title = 'DAOKO × Kenshi Yonezu “Fireworks” MUSIC VIDEO'
        self.cards = [card('打上花火', 'DAOKO×米津玄師', '打上花火')]
        artwork.resolve(item, {'title': item.title, 'thumbnail': 'http://video.jpg'}, providers=[lambda q: []])
        self.assertEqual(('video', '打上花火', 'DAOKO×米津玄師', '打上花火'),
                         (item.artwork, item.track, item.artist, item.album))
        self.assertEqual('pending', item.shazam)

    def test_covers_skip_card_and_shazam(self):
        item = FakeURLItem()
        item.title = '【翻唱】Lemon 米津玄师'
        self.audio(item)
        artwork.resolve(item, {'title': item.title, 'thumbnail': 'http://video.jpg'}, providers=[lambda q: []])
        self.assertEqual([], self.card_lookups)
        self.assertEqual([], self.shazam_calls)
        self.assertEqual('', item.shazam)

    def test_shazam_waits_for_download(self):
        item = FakeURLItem()
        item.title = 'some untitled upload'
        self.heard = {'title': 'Lemon', 'artists': ['米津玄师'], 'album': 'STRAY SHEEP',
                      'image': 'http://shazam/1000x1000cc.jpg', 'source': 'shazam'}
        artwork.resolve(item, {'title': item.title, 'thumbnail': 'http://video.jpg'}, providers=[lambda q: []])
        self.assertEqual(('video', 'pending'), (item.artwork, item.shazam))
        self.assertEqual([], self.shazam_calls)
        # 还没下载完:不排队
        artwork.ensure(item)
        self.assertNotIn(item.id, artwork._queued)
        # 下载完了:排队,轮到时只听歌识曲,不再查卡片
        self.audio(item)
        artwork.ensure(item)
        self.assertIn(item.id, artwork._queued)
        with artwork._lock:
            artwork._queued.discard(item.id)
        lookups = len(self.card_lookups)
        artwork.resolve(item, None, providers=[lambda q: []])
        self.assertEqual([item.path], self.shazam_calls)
        self.assertEqual(lookups, len(self.card_lookups))
        # 曲库没有:用 Shazam 自己的封面
        self.assertEqual(('album', 'Lemon', '米津玄师', 'STRAY SHEEP', 'done'),
                         (item.artwork, item.track, item.artist, item.album, item.shazam))
        self.assertIn('http://shazam/1000x1000cc.jpg', self.fetched)
        # 只试一次
        artwork.ensure(item)
        self.assertNotIn(item.id, artwork._queued)

    def test_shazam_result_looked_up_in_catalog(self):
        item = FakeURLItem()
        item.title = 'some untitled upload'
        self.audio(item)
        self.heard = {'title': 'Lemon', 'artists': ['米津玄师'], 'album': 'STRAY SHEEP',
                      'image': 'http://shazam/1000x1000cc.jpg', 'source': 'shazam'}
        queries = []

        def provider(q):
            queries.append(q)
            return [song('Lemon', ['米津玄師'], 255, album='STRAY SHEEP', image='http://catalog.jpg')] \
                if 'Lemon' in q else []
        artwork.resolve(item, {'title': item.title, 'thumbnail': 'http://video.jpg'}, providers=[provider])
        # 已经缓存过的歌:同一轮里就认
        self.assertEqual([item.path], self.shazam_calls)
        self.assertEqual(['some untitled upload', 'Lemon'], queries)
        self.assertEqual(('album', 'Lemon', '米津玄師'), (item.artwork, item.track, item.artist))
        self.assertEqual('http://catalog.jpg', self.fetched[-1])

    def test_shazam_nothing_heard(self):
        item = FakeURLItem()
        item.title = 'some untitled upload'
        self.audio(item)
        artwork.resolve(item, {'title': item.title, 'thumbnail': 'http://video.jpg'}, providers=[lambda q: []])
        self.assertEqual(('video', '', 'done'), (item.artwork, item.track, item.shazam))

    def test_shazam_retried_after_network_failure(self):
        item = FakeURLItem()
        item.title = 'some untitled upload'
        self.audio(item)

        def offline(path, duration=0, url=''):
            raise recognize.ShazamUnavailable()
        recognize.shazam = offline
        artwork.resolve(item, {'title': item.title, 'thumbnail': 'http://video.jpg'}, providers=[lambda q: []])
        self.assertEqual('pending', item.shazam)
        artwork.ensure(item)
        self.assertIn(item.id, artwork._queued)
        with artwork._lock:
            artwork._queued.discard(item.id)

    def test_shazam_disabled(self):
        var.config.set('bot', 'artwork_shazam', 'False')
        item = FakeURLItem()
        self.audio(item)
        artwork.resolve(item, {'title': item.title, 'thumbnail': 'http://video.jpg'}, providers=[lambda q: []])
        self.assertEqual([], self.shazam_calls)
        self.assertEqual('', item.shazam)

    def test_old_records_without_album_looked_up_again(self):
        # 加「Music」卡片和听歌识曲之前查过、没找到专辑封面的:再查一次
        item = FakeURLItem()
        item.artwork, item.artwork_done, item.track = 'video', True, ''
        del item.shazam
        with open(artwork.path_for(item.id), 'wb') as f:
            f.write(b'x')
        artwork.ensure(item)
        self.assertIn(item.id, artwork._queued)
        with artwork._lock:
            artwork._queued.discard(item.id)
        # 找到了专辑封面的不动
        item.artwork = 'album'
        artwork.ensure(item)
        self.assertNotIn(item.id, artwork._queued)

    def test_prune_removes_oldest(self):
        var.config.set('bot', 'artwork_folder_max_size', '1')
        paths = []
        for i in range(3):
            p = os.path.join(self.tmp.name, f'{i:032x}.jpg')
            with open(p, 'wb') as f:
                f.write(b'0' * 600 * 1024)
            os.utime(p, (1000 + i, 1000 + i))
            paths.append(p)
        artwork.prune()
        self.assertFalse(os.path.exists(paths[0]))
        self.assertFalse(os.path.exists(paths[1]))
        self.assertTrue(os.path.exists(paths[2]))


if __name__ == '__main__':
    unittest.main()
