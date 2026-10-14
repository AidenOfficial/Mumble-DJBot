#!/usr/bin/python3
import hmac
import html
import json
import logging
import os
from functools import wraps

from flask import Flask, render_template, request, redirect, send_from_directory, Response, abort, session, g

import util
import variables as var
from constants import tr_web


class ReverseProxied(object):
    """Wrap the application in this middleware and configure the
    front-end server to add these headers, to let you quietly bind
    this to a URL other than / and to an HTTP scheme that is
    different than what is used locally.

    In nginx:
    location /myprefix {
        proxy_pass http://192.168.0.1:5001;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Scheme $scheme;
        proxy_set_header X-Script-Name /myprefix;
        }

    :param app: the WSGI application
    """

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        script_name = environ.get('HTTP_X_SCRIPT_NAME', '')
        if script_name:
            environ['SCRIPT_NAME'] = script_name
            path_info = environ['PATH_INFO']
            if path_info.startswith(script_name):
                environ['PATH_INFO'] = path_info[len(script_name):]

        scheme = environ.get('HTTP_X_SCHEME', '')
        if scheme:
            environ['wsgi.url_scheme'] = scheme
        real_ip = environ.get('HTTP_X_REAL_IP', '')
        if real_ip:
            environ['REMOTE_ADDR'] = real_ip
        return self.app(environ, start_response)


import web_api
import web_cache
import web_channels
import web_library
import web_logs
import web_upload
import web_users

root_dir = os.path.dirname(__file__)
web = Flask(__name__, template_folder=os.path.join(root_dir, "templates"))
log = logging.getLogger("bot")


def init_proxy():
    if var.is_proxified:
        web.wsgi_app = ReverseProxied(web.wsgi_app)


# https://stackoverflow.com/questions/29725217/password-protect-one-webpage-in-flask-app


def check_auth(username, password):
    """This function is called to check if a username /
    password combination is valid.
    """

    # 配置里的账号:两项都填了才生效,否则空用户名+空密码的 Basic Auth 也能登录
    conf_user = var.config.get("webinterface", "user")
    conf_password = var.config.get("webinterface", "password")
    if conf_user and conf_password and \
            hmac.compare_digest((username or "").encode(), conf_user.encode()) and \
            hmac.compare_digest((password or "").encode(), conf_password.encode()):
        return True

    web_users = json.loads(var.db.get("privilege", "web_access", fallback='[]'))
    if username in web_users:
        user_dict = json.loads(var.db.get("user", username, fallback='{}'))
        if 'password' in user_dict and 'salt' in user_dict and \
                util.verify_password(password, user_dict['password'], user_dict['salt']):
            return True

    return False


def authenticate():
    """Sends a 401 response that enables basic auth"""
    return Response('Could not verify your access level for that URL.\n'
                    'You have to login with proper credentials', 401,
                    {'WWW-Authenticate': 'Basic realm="Login Required"'})


bad_access_count = {}
banned_ip = []


def _with_identity(f, auth_user, args, kwargs):
    # 鉴权通过后解析身份(Cloudflare Access 邮箱 / Web 用户名),供点歌署名和个人歌单使用
    g.web_identity = web_users.resolve_identity(auth_user)
    return f(*args, **kwargs)


def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.remote_addr in banned_ip:
            abort(403)

        auth_method = var.config.get("webinterface", "auth_method")

        if auth_method == 'password':
            auth = request.authorization
            if auth:
                if not check_auth(auth.username, auth.password):
                    if request.remote_addr in bad_access_count:
                        bad_access_count[request.remote_addr] += 1
                        log.info(f"web: failed login attempt, user: {auth.username}, from ip {request.remote_addr}."
                                 f"{bad_access_count[request.remote_addr]} attempts.")
                        if bad_access_count[request.remote_addr] > var.config.getint("webinterface", "max_attempts",
                                                                                     fallback=10):
                            banned_ip.append(request.remote_addr)
                            log.info(f"web: access banned for {request.remote_addr}")
                    else:
                        bad_access_count[request.remote_addr] = 1
                        log.info(f"web: failed login attempt, user: {auth.username}, from ip {request.remote_addr}.")
                    return authenticate()
            else:
                return authenticate()
        if auth_method == 'token':
            if 'user' in session and 'token' not in request.args:
                return _with_identity(f, session['user'], args, kwargs)
            elif 'token' in request.args:
                token = request.args.get('token')
                token_user = var.db.get("web_token", token, fallback=None)
                if token_user is not None:
                    user_info = var.db.get("user", token_user, fallback=None)
                    user_dict = json.loads(user_info)
                    user_dict['IP'] = request.remote_addr
                    var.db.set("user", token_user, json.dumps(user_dict))

                    log.debug(
                        f"web: new user access, token validated for the user: {token_user}, from ip {request.remote_addr}.")
                    session['token'] = token
                    session['user'] = token_user
                    return _with_identity(f, token_user, args, kwargs)

            if request.remote_addr in bad_access_count:
                bad_access_count[request.remote_addr] += 1
                log.info(f"web: bad token from ip {request.remote_addr}, "
                         f"{bad_access_count[request.remote_addr]} attempts.")
                if bad_access_count[request.remote_addr] > var.config.getint("webinterface", "max_attempts"):
                    banned_ip.append(request.remote_addr)
                    log.info(f"web: access banned for {request.remote_addr}")
            else:
                bad_access_count[request.remote_addr] = 1
                log.info(f"web: bad token from ip {request.remote_addr}.")

            command = f"{var.config.get('commands', 'command_symbol')[0]}" \
                      f"{var.config.get('commands', 'requests_webinterface_access')}"
            return render_template('need_token.html',
                                   lang=var.language.split('_')[0],
                                   title=tr_web('token_required'),
                                   message=tr_web('token_required_message',
                                                  name=html.escape(var.config.get('bot', 'username')),
                                                  command=html.escape(command)),
                                   token_label=tr_web('token'),
                                   submit=tr_web('submit'))

        auth_user = request.authorization.username \
            if auth_method == 'password' and request.authorization else None
        return _with_identity(f, auth_user, args, kwargs)

    return decorated


# JSON API for the new web interface (all routes under /api/)
web.register_blueprint(web_api.create_blueprint(requires_auth))
web.register_blueprint(web_users.create_blueprint(requires_auth))
web.register_blueprint(web_cache.create_blueprint(requires_auth))
web.register_blueprint(web_upload.create_blueprint(requires_auth))
web.register_blueprint(web_channels.create_blueprint(requires_auth))
web.register_blueprint(web_library.create_blueprint(requires_auth))
web.register_blueprint(web_logs.create_blueprint(requires_auth))


# The web interface: webui/ (Vue + Vite), built into webui/dist.
webui_dist = os.path.join(root_dir, "webui", "dist")


@web.route("/", methods=['GET'])
@requires_auth
def index():
    if not os.path.isfile(os.path.join(webui_dist, "index.html")):
        return "Web interface not built (run `npm ci && npm run build` in webui/).", 404
    return send_from_directory(webui_dist, "index.html")


@web.route("/assets/<path:path>", methods=['GET'])
@requires_auth
def webui_assets(path):
    return send_from_directory(os.path.join(webui_dist, "assets"), path)


@web.route("/favicon.svg", methods=['GET'])
def webui_favicon():
    return send_from_directory(webui_dist, "favicon.svg")


# Old mount point of the new UI - kept so bookmarks keep working.
@web.route("/app", methods=['GET'])
@requires_auth
def webui_root():
    return redirect("./app/", code=302)


@web.route("/app/", defaults={'path': 'index.html'}, methods=['GET'])
@web.route("/app/<path:path>", methods=['GET'])
@requires_auth
def webui_app(path):
    if not os.path.isdir(webui_dist):
        abort(404)
    return send_from_directory(webui_dist, path)
