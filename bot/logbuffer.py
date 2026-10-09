"""Web 日志面板的数据源:内存环形缓冲 + 磁盘 JSONL 历史。

- 挂在 "bot" logger 上收全部级别;另挂一份到 root logger,只收第三方库
  (pymumble、Flask 路由异常等)WARNING 以上的记录。
- 每条记录同时追加写入 <settings 库所在目录>/logs/bot-log.jsonl(按大小滚动)。
  bot 崩溃 / 被 watchdog 杀掉重启后,启动时把上一轮的尾部读回缓冲,
  面板上还能看到崩溃前发生了什么。
- 调试模式:临时把 "bot" logger 调到 DEBUG,到时间自动恢复,避免忘关刷爆磁盘。
"""

import collections
import json
import logging
import os
import threading
import time
import uuid

CAPACITY = 3000            # 内存里最多保留的条数
HISTORY_LOAD = 1500        # 启动时从磁盘读回的上一轮条数上限
HISTORY_MAX_BYTES = 2 * 1024 * 1024
HISTORY_BACKUPS = 2
MSG_MAX_LEN = 8000
DEBUG_MINUTES = 30

LEVELS = {'DEBUG': logging.DEBUG, 'INFO': logging.INFO, 'WARNING': logging.WARNING,
          'ERROR': logging.ERROR, 'CRITICAL': logging.CRITICAL}


def _truncate(text, limit=MSG_MAX_LEN):
    return text if len(text) <= limit else text[:limit] + f"… [{len(text) - limit} more chars]"


class _History:
    """极简的按大小滚动 JSONL 写入器(每条都 flush,进程被 os._exit 也不丢)。"""

    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.fp = open(path, 'a', encoding='utf-8')

    def write(self, entry):
        self.fp.write(json.dumps(entry, ensure_ascii=False) + '\n')
        self.fp.flush()
        if self.fp.tell() > HISTORY_MAX_BYTES:
            self._rotate()

    def _rotate(self):
        self.fp.close()
        for i in range(HISTORY_BACKUPS, 0, -1):
            src = self.path if i == 1 else f"{self.path}.{i - 1}"
            if os.path.exists(src):
                os.replace(src, f"{self.path}.{i}")
        self.fp = open(self.path, 'a', encoding='utf-8')

    def read_tail(self, limit):
        lines = []
        for i in range(HISTORY_BACKUPS, -1, -1):
            p = self.path if i == 0 else f"{self.path}.{i}"
            try:
                with open(p, encoding='utf-8', errors='replace') as f:
                    lines.extend(f.readlines())
            except OSError:
                continue
        entries = []
        for line in lines[-limit:]:
            try:
                entry = json.loads(line)
            except ValueError:
                continue  # 崩溃时写了半行
            if isinstance(entry, dict) and 'msg' in entry:
                entries.append(entry)
        return entries


class LogBuffer(logging.Handler):
    def __init__(self, capacity=CAPACITY):
        super().__init__(level=logging.DEBUG)
        self.records = collections.deque(maxlen=capacity)
        self.seq = 0
        self.run_id = uuid.uuid4().hex[:8]
        self.started_at = time.time()
        self.history = None
        self.debug_until = None
        self._debug_timer = None
        self._normal_level = logging.INFO

    # ---- 写入 ---------------------------------------------------------------

    def _entry(self, record):
        try:
            msg = record.getMessage()
        except Exception:
            msg = f"{record.msg!r} % {record.args!r}"
        exc = None
        if record.exc_info and record.exc_info[0] is not None:
            exc = logging.Formatter().formatException(record.exc_info)
        elif record.stack_info:
            exc = record.stack_info
        return {
            'ts': record.created,
            'level': record.levelname,
            'name': record.name,
            'msg': _truncate(msg),
            'exc': _truncate(exc, 20000) if exc else None,
            'src': f"{record.filename}:{record.lineno}",
            'thread': record.threadName,
            'run': self.run_id,
        }

    def emit(self, record):
        # Handler.handle() 已经持有 self.lock
        try:
            entry = self._entry(record)
            self.seq += 1
            entry['seq'] = self.seq
            self.records.append(entry)
            if self.history is not None:
                self.history.write(entry)
        except Exception:
            self.handleError(record)

    def attach_history(self, path):
        """开始持久化,并把上一轮留下的日志读回缓冲(放在本轮已有记录之前)。"""
        try:
            history = _History(path)
        except OSError:
            logging.getLogger('bot').warning("log: cannot write log history to %s", path, exc_info=True)
            return
        self.acquire()
        try:
            previous = [e for e in history.read_tail(HISTORY_LOAD) if e.get('run') != self.run_id]
            current = list(self.records)
            self.records.clear()
            self.seq = 0
            for entry in previous + current:
                self.seq += 1
                entry['seq'] = self.seq
                self.records.append(entry)
            for entry in current:  # 历史文件里补上本轮启动早期的记录
                history.write(entry)
            self.history = history
        finally:
            self.release()

    # ---- 查询 ---------------------------------------------------------------

    def query(self, after=0, min_level='DEBUG', q='', limit=500):
        """返回 seq > after、级别不低于 min_level、包含 q 的记录(最多最后 limit 条)。"""
        threshold = LEVELS.get(str(min_level).upper(), logging.DEBUG)
        needle = (q or '').strip().lower()
        self.acquire()
        try:
            snapshot = list(self.records)
            last_seq = self.seq
        finally:
            self.release()
        out = []
        for e in snapshot:
            if e['seq'] <= after or LEVELS.get(e['level'], 0) < threshold:
                continue
            if needle and needle not in e['msg'].lower() and needle not in (e['exc'] or '').lower() \
                    and needle not in e['name'].lower():
                continue
            out.append(e)
        truncated = len(out) > limit
        return {'entries': out[-limit:], 'last_seq': last_seq, 'truncated': truncated}

    # ---- 调试模式 -------------------------------------------------------------

    def set_debug(self, enabled, minutes=DEBUG_MINUTES):
        bot_logger = logging.getLogger('bot')
        if self._debug_timer:
            self._debug_timer.cancel()
            self._debug_timer = None
        if enabled:
            if self.debug_until is None:
                self._normal_level = bot_logger.level
            bot_logger.setLevel(logging.DEBUG)
            self.debug_until = time.time() + minutes * 60
            self._debug_timer = threading.Timer(minutes * 60, self.set_debug, args=(False,))
            self._debug_timer.daemon = True
            self._debug_timer.start()
            bot_logger.info("log: debug logging on for %d minutes", minutes)
        elif self.debug_until is not None:
            self.debug_until = None
            bot_logger.setLevel(self._normal_level)
            bot_logger.info("log: debug logging off")

    def state(self):
        return {
            'level': logging.getLevelName(logging.getLogger('bot').getEffectiveLevel()),
            'debug_until': self.debug_until,
            'run': self.run_id,
            'started_at': self.started_at,
            'persistent': self.history is not None,
        }


class _ThirdPartyFilter(logging.Filter):
    """root 上的那份只收第三方的 WARNING+:bot / werkzeug 都不往 root 传播,这里再保险一次。"""

    def filter(self, record):
        return record.levelno >= logging.WARNING and record.name not in ('bot', 'werkzeug')


class _RootTap(logging.Handler):
    def __init__(self, buffer):
        super().__init__(level=logging.WARNING)
        self.buffer = buffer
        self.addFilter(_ThirdPartyFilter())

    def emit(self, record):
        self.buffer.handle(record)


buffer = LogBuffer()


def install(bot_logger):
    """启动时调用一次(在配置好 bot logger 之后)。"""
    bot_logger.addHandler(buffer)
    root = logging.getLogger()
    if not root.handlers:
        # root 原本没有 handler 时,第三方 WARNING+ 走的是 logging.lastResort(stderr);
        # 一旦挂上 _RootTap,lastResort 就不再生效,Flask 也会因此不装它自己的 stderr handler。
        # 补一个等价的 stderr 输出,docker logs 里照旧能看到这些。
        fallback = logging.StreamHandler()
        fallback.setLevel(logging.WARNING)
        root.addHandler(fallback)
    root.addHandler(_RootTap(buffer))
    return buffer
