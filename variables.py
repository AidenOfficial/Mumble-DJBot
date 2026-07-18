from typing import Type, TYPE_CHECKING

if TYPE_CHECKING:
    import bot.cleanup
    import bot.core
    import media.playlist
    import media.cache
    import database

bot: 'bot.core.MumbleBot' = None
playlist: 'media.playlist.BasePlaylist' = None
cache: 'media.cache.MusicCache' = None
cleaner: 'bot.cleanup.CacheCleaner' = None

user = ""
is_proxified = False

settings_db_path = None
music_db_path = None
db = None
music_db: 'database.MusicDatabase' = None
config: 'database.SettingsDatabase' = None

bot_logger = None

music_folder = ""
tmp_folder = ""

language = ""
