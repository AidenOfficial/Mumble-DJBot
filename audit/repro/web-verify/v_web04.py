from v_common import *
i = setup('none')
import constants; constants.load_lang('en_US')
import media.url
from media.url import URLItem
evil = 'http://example.com/a"><img src=x onerror=alert(document.domain)>'
it = URLItem(evil)
it.title = '<img src=x onerror=alert(2)>'      # titles come from remote metadata (yt-dlp)
it.tags = []
class W:
    def __init__(s, it): s._i = it; s.id = it.id
    def item(s): return s._i
var.playlist = FakePlaylist([W(it)])
var.playlist.current_index = 0
i.build_tags_color_lookup = lambda: {}
c = i.web.test_client()
r = c.get('/playlist')
j = r.get_json()
print(r.status_code); print('path :', j['items'][0]['path']); print('title:', j['items'][0]['title'])
# does the add path sanitise? (util.get_url_from_input is what chat uses; web add_url does NOT call it)
import util
print('get_url_from_input keeps payload:', util.get_url_from_input(evil))
