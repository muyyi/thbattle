# -*- coding: utf-8 -*-

import sys
import logging
import cStringIO

tee = None
debug_logfile = cStringIO.StringIO()


class Tee(object):
    def __init__(self):
        self.logfile = f = open('client_log.txt', 'a+')
        self.history = []
        import datetime
        s = (
            '\n' + datetime.datetime.now().strftime("%Y-%m-%d %H:%M") +
            '\n==============================================\n'
        )
        self.history.append(s)
        f.write(s)

    def write(self, v):
        sys.__stdout__.write(v)
        self.history.append(v)
        self.logfile.write(v.encode('utf-8'))


def install_tee(level):
    global tee
    tee = sys.stderr = sys.stdout = Tee()

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    hldr = logging.StreamHandler(tee)
    hldr.setLevel(getattr(logging, level))
    root.addHandler(hldr)

    hldr = logging.StreamHandler(debug_logfile)
    hldr.setLevel(logging.DEBUG)
    root.addHandler(hldr)


def do_crashreport(active=False):
    import requests
    import zlib
    import traceback

    s = u''.join(tee.history)
    s += u'\n\n\nException:\n' + '=' * 80 + '\n' + traceback.format_exc()
    import pyglet.info
    s += u'\n\n\nPyglet info:\n' + pyglet.info.dump()
    debug_logfile.seek(0)
    debug_log = debug_logfile.read()
    s += u'\n\n\nDebug log:\n' + '=' * 80 + '\n' + debug_log
    content = zlib.compress(s.encode('utf-8'))

    try:
        from game.autoenv import Game
        g = Game.getgame()
        gameid = g.gameid
    except:
        gameid = 0

    try:
        from client.core import Executive
        userid = Executive.gamemgr.account.userid
        username = Executive.gamemgr.account.username
    except:
        userid = 0
        username = u'unknown'

    requests.post(
        'http://www.thbattle.net/interconnect/crashreport',
        data={
            'gameid': gameid,
            'active': int(active),
            'userid': userid,
            'username': username,
        },
        files={'file': content},
    )
