# 复现脚本(repro)

这些脚本在审计中用于证明各发现成立。全部只读仓库、不改任何文件,写入都落在系统临时目录或脚本自带的 `sandbox/`。

## 运行环境

```bash
python3 -m venv ~/venv
~/venv/bin/pip install pytest flask requests mutagen Pillow python-magic packaging pyradios opencc-python-reimplemented yt-dlp "PyJWT[crypto]" audioop-lts
```

Python 3.13 删除了 `audioop`,`audioop-lts` 是其替身;`pymumble` 测试不依赖,未装。**所有命令都从仓库根目录执行。**

## 各发现的规范脚本

| 发现 | 脚本 | 运行 |
|---|---|---|
| WEB-01/02 鉴权绕过 / 封禁绕过 | `web-verify/v_web01_02.py` | `python audit/repro/web-verify/v_web01_02.py` |
| WEB-03 SSRF | `web/repro_ssrf2.py`、`web-verify/v_web03.py` | `python audit/repro/web/repro_ssrf2.py` |
| WEB-04 旧前端 XSS | `web-verify/v_web04.py` | 同上 |
| WEB-07 上传路径穿越 / 任意写 | `web/repro_upload.py`、`web-verify/v_web07.py` | `python audit/repro/web/repro_upload.py` |
| WEB-08 CSRF / SameSite | `web-verify/v_web08.py` | 同上 |
| WEB-10 /library rmdir 穿越 | `web-redteam/repro_rmdir_traversal.py` | 同上 |
| WEB-11 上传配额耗尽 | `web-redteam/repro_upload_space.py` | 同上 |
| WEB-12 绑定码劫持 | `web-redteam/repro_bind.py` | 同上 |
| WEB-13 旧路由 500 | `web-redteam/repro_500.py` | 同上 |
| PC-01/02/03 等播放核心 | `playback/repro_playback_core.py`、`playback/repro_playback_audit.py` | `python -m pytest audit/repro/playback/*.py` |
| F1/DL-1/RACE-1 等清理与库 | `data/repro_cleanup_data.py`、`data/repro_deep_cleanup.py` | `python -m pytest audit/repro/data/repro_cleanup_data.py audit/repro/data/repro_deep_cleanup.py` |
| DB-1 dropdatabase | `data/drop_db_probe.py` | `python audit/repro/data/drop_db_probe.py` |
| DB-2 / CJK 标题 | `data/probe_misc.py` | 同上 |
| CCD-01 ReDoS | `commands/verify.py`、`commands/repro_misc.py` | `python audit/repro/commands/verify.py` |
| CCD-02 `!repeat` 无上限 | `commands/repro_repeat.py` | `python audit/repro/commands/repro_repeat.py` |
| CCD-03 admin 冒名 / joinme | `commands/repro_dispatch.py` | `python audit/repro/commands/repro_dispatch.py` |
| CCD-04/11/20/23 杂项 | `commands/verify.py`、`commands/repro_misc.py` | 同上 |
| 配置三源一致性 | `commands/cfgcheck.py`、`commands/config_keys.py` | 同上 |

pytest 套件有两种写法,脚本顶部注释写明了每条断言的含义:

- `repro_playback_core.py`、`repro_cleanup_data.py`、`repro_deep_cleanup.py` 断言**安全/正确行为**,**失败 = 复现成功**。`repro_playback_core.py` 中有 3 条反向断言,通过即复现。
- `repro_playback_audit.py` 断言**当前的错误行为**,**通过 = 复现成功**。

这些文件刻意不以 `test_` 开头,仓库根直接运行 `pytest` 不会收集它们。

## 约定

- 脚本用 `os.getcwd()` 作为仓库根,所以必须在仓库根运行。
- `web/repro_ssrf.py`、`web/repro_auth.py`、`web/repro_legacy.py` 是早期版本,核心结论已被上表的规范脚本覆盖;它们末尾可能因缺少 `need_token.<lang>.html` 模板(该模板不在 git 内)或假对象缺方法而报错,但报错之前打印的结论有效。
