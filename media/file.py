import os
import re
from io import BytesIO
import base64
import hashlib
import mutagen
import mutagen.flac
from PIL import Image

import util
import variables as var
from media.item import BaseItem, item_builders, item_loaders, item_id_generators, ValidationFailedError
from constants import tr_cli as tr

'''
type : file
    id
    path
    title
    artist
    duration
    thumbnail
    user
'''


def file_item_builder(**kwargs):
    return FileItem(kwargs['path'])


def file_item_loader(_dict):
    return FileItem("", _dict)


def file_item_id_generator(**kwargs):
    return hashlib.md5(kwargs['path'].encode()).hexdigest()


item_builders['file'] = file_item_builder
item_loaders['file'] = file_item_loader
item_id_generators['file'] = file_item_id_generator


class FileItem(BaseItem):
    def __init__(self, path, from_dict=None):
        if not from_dict:
            super().__init__()
            self.path = path
            self.title = ""
            self.artist = ""
            self.thumbnail = None
            self.artwork = ""  # 高清封面来源,见 media/artwork.py
            self.artwork_done = False
            self.id = hashlib.md5(path.encode()).hexdigest()
            if os.path.exists(self.uri()):
                self._get_info_from_tag()
                self.ready = "yes"
                self.duration = util.get_media_duration(self.uri())
            self.keywords = self.title + " " + self.artist
        else:
            super().__init__(from_dict)
            self.artist = from_dict['artist']
            self.thumbnail = from_dict['thumbnail']
            self.artwork = from_dict.get('artwork', '')
            self.artwork_done = from_dict.get('artwork_done', False)
            try:
                self.validate()
            except ValidationFailedError:
                self.ready = "failed"

        self.type = "file"

    def uri(self):
        return var.music_folder + self.path if self.path[0] != "/" else self.path

    def is_ready(self):
        return True

    def validate(self):
        if not os.path.exists(self.uri()):
            self.log.info(
                "file: music file missed for %s" % self.format_debug_string())
            raise ValidationFailedError(tr('file_missed', file=self.path))

        if self.duration == 0:
            self.duration = util.get_media_duration(self.uri())
            self.version += 1  # 0 -> 1, notify the wrapper to save me
        self.ready = "yes"
        return True

    def _get_info_from_tag(self):
        file_name = os.path.splitext(os.path.basename(self.uri()))[0]
        try:
            tags = mutagen.File(self.uri())
            ext = os.path.splitext(self.uri())[1].lower()
            if tags is not None:
                if ext == ".mp3":
                    # title: TIT2, artist: TPE1
                    if 'TIT2' in tags:
                        self.title = tags['TIT2'].text[0]
                    if 'TPE1' in tags:
                        self.artist = tags['TPE1'].text[0]
                elif ext in (".m4a", ".m4b", ".mp4", ".m4p"):
                    # title: ©nam, artist: ©ART
                    if '©nam' in tags:
                        self.title = tags['©nam'][0]
                    if '©ART' in tags:
                        self.artist = tags['©ART'][0]
                elif ext in (".opus", ".flac"):
                    if 'title' in tags:
                        self.title = tags['title'][0]
                    if 'artist' in tags:
                        self.artist = tags['artist'][0]
        except Exception:
            pass

        try:
            im = self.cover_image()
            if im is not None:
                self.thumbnail = self._prepare_thumbnail(im)
        except Exception:
            pass

        if not self.title:
            self.title = file_name

    def cover_image(self):
        """封面原图(PIL Image),没有返回 None。顺序:同名 .jpg、目录下 cover.jpg、文件内嵌封面。
        这里只读文件;高清版由 media/artwork.py 在需要时另存。"""
        path, file_name_ext = os.path.split(self.uri())
        file_name, ext = os.path.splitext(file_name_ext)
        ext = ext.lower()
        for sidecar in (os.path.join(path, file_name + ".jpg"), os.path.join(path, "cover.jpg")):
            if os.path.isfile(sidecar):
                im = Image.open(sidecar)
                im.load()
                return im
        try:
            tags = mutagen.File(self.uri())
        except Exception:
            return None
        if tags is None:
            return None
        data = None
        if ext == ".mp3":
            # cover artwork: APIC:(有的文件带描述,键名是 "APIC:Cover")
            apic = [k for k in tags.keys() if k.startswith('APIC')]
            if apic:
                data = tags[apic[0]].data
        elif ext in (".m4a", ".m4b", ".mp4", ".m4p"):
            if "covr" in tags:
                data = bytes(tags["covr"][0])
        elif ext == ".opus":
            # metadata_block_picture: base64 编码的 FLAC Picture 结构
            if 'metadata_block_picture' in tags:
                pic = mutagen.flac.Picture(base64.b64decode(tags['metadata_block_picture'][0]))
                data = pic.data
        elif ext == ".flac":
            pictures = getattr(tags, 'pictures', [])
            front = [p for p in pictures if p.type == 3] or pictures
            if front:
                data = front[0].data
        if not data:
            return None
        im = Image.open(BytesIO(data))
        im.load()
        return im

    @staticmethod
    def _prepare_thumbnail(im):
        im.thumbnail((100, 100), Image.LANCZOS)
        buffer = BytesIO()
        im = im.convert('RGB')
        im.save(buffer, format="JPEG")
        return base64.b64encode(buffer.getvalue()).decode('utf-8')

    def to_dict(self):
        dict = super().to_dict()
        dict['type'] = 'file'
        dict['path'] = self.path
        dict['title'] = self.title
        dict['artist'] = self.artist
        dict['thumbnail'] = self.thumbnail
        dict['artwork'] = self.artwork
        dict['artwork_done'] = self.artwork_done
        return dict

    def format_debug_string(self):
        return "[file] {descrip} ({path})".format(
            descrip=self.format_title(),
            path=self.path
        )

    def format_song_string(self, user):
        return tr("file_item",
                  title=self.title,
                  artist=self.artist if self.artist else '??',
                  user=user
                  )

    def format_current_playing(self, user):
        display = tr("now_playing", item=self.format_song_string(user))
        if self.thumbnail:
            thumbnail_html = '<img width="80" src="data:image/jpge;base64,' + \
                             self.thumbnail + '"/>'
            display += "<br />" + thumbnail_html

        return display

    def format_title(self):
        title = self.title if self.title else self.path
        if self.artist:
            return self.artist + " - " + title
        else:
            return title

    def display_type(self):
        return tr("file")
