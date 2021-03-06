# -*- coding: utf-8 -*-

# -- stdlib --
import logging

# -- third party --
from gevent import socket, Greenlet
from gevent.pool import Pool
import gevent

# -- own --
from account import Account
from autoupdate import Autoupdate
from network.client import Server
from utils import BatchList, instantiate

# -- code --
log = logging.getLogger('Executive')


class ForcedKill(gevent.GreenletExit):
    pass


class GameManager(Greenlet):
    '''
    Handles server messages, all game related operations.
    '''
    def __init__(self, event_cb):
        Greenlet.__init__(self)
        self.state     = 'connected'
        self.game      = None
        self.last_game = None
        self.event_cb  = event_cb

    def _run(self):
        self.link_exception(lambda *a: self.event_cb('server_dropped'))

        from gamepack import gamemodes
        handlers = {}

        def handler(_from, _to):
            def register(f):
                handlers[f.__name__] = (f, _from, _to)
            return register

        @handler(('inroom', 'ingame'), None)
        def player_change(self, data):
            self.players_data = data
            if self.state == 'ingame':
                data1 = []
                for p in data:
                    acc = Account.parse(p['account'])
                    for i, pl in enumerate(self.game.players):
                        if pl.account.userid != acc.userid: continue
                        data1.append(p)
                        self.game.players[i].dropped = (p['state'] in ('dropped', 'fleed'))

                self.event_cb('player_change', data1)
            else:
                self.event_cb('player_change', data)

        @handler(('inroom',), 'ingame')
        def game_started(self, data):
            params, pldata = data
            Executive.server.gclear()
            if self.last_game:
                self.last_game.kill(ForcedKill)
                self.last_game.get()
                self.last_game = None

            from client.core import PeerPlayer, TheChosenOne
            pl = [PeerPlayer.parse(i) for i in pldata]
            pid = [i.account.userid for i in pl]
            me = TheChosenOne(Executive.server)
            me.account = self.account
            i = pid.index(me.account.userid)
            pl[i] = me
            g = self.game
            g.me = me
            g.game_params = params
            g.players = BatchList(pl)
            # g.start()  Starts by UI
            log.info('=======GAME STARTED: %d=======' % g.gameid)
            log.info(g)

            @g.link_exception
            def crash(*a):
                self.event_cb('game_crashed', g)

            @g.link_value
            def finish(*a):
                v = g.get()
                if not isinstance(v, ForcedKill):
                    self.event_cb('client_game_finished', g)

            self.event_cb('game_started', g)

        @handler(('inroom',), 'ingame')
        def observe_started(self, data):
            Executive.server.gclear()
            if self.last_game:
                self.last_game.kill(ForcedKill)
                self.last_game.get()
                self.last_game = None

            params, tgtid, pldata = data
            from client.core import PeerPlayer, TheLittleBrother
            pl = [PeerPlayer.parse(i) for i in pldata]
            pid = [i.account.userid for i in pl]
            i = pid.index(tgtid)
            g = self.game
            g.players = BatchList(pl)
            me = g.players[i]
            me.__class__ = TheLittleBrother
            me.server = Executive.server
            g.me = me
            g.game_params = params
            # g.start()  Starts by UI
            log.info('=======OBSERVE STARTED=======')
            log.info(g)

            @g.link_exception
            def crash(*a):
                self.event_cb('game_crashed', g)

            @g.link_value
            def finish(*a):
                v = g.get()
                if not isinstance(v, ForcedKill):
                    self.event_cb('client_game_finished', g)

            self.event_cb('game_started', g)

        @handler(('hang', 'inroom'), 'inroom')
        def game_joined(self, data):
            self.game = gamemodes[data['type']]()
            self.game.gameid = int(data['id'])
            self.event_cb('game_joined', self.game)
            self.event_cb('game_params', data['params'])

        @handler(('ingame',), 'hang')
        def fleed(self, data):
            self.game.kill(ForcedKill)
            self.game = None
            log.info('=======FLEED=======')
            Executive.server.gclear()
            self.event_cb('fleed')

        @handler(('ingame', 'inroom'), 'hang')
        def game_left(self, data):
            self.game.kill(ForcedKill)
            self.game = None
            log.info('=======GAME LEFT=======')
            Executive.server.gclear()
            self.event_cb('game_left')

        @handler(('ingame',), 'hang')
        def end_game(self, data):
            self.event_cb('end_game', self.game)
            log.info('=======GAME ENDED=======')
            self.last_game = self.game

        @handler(('connected',), None)
        def auth_result(self, status):
            if status == 'success':
                self.event_cb('auth_success')
                self.state = 'hang'
            else:
                self.event_cb('auth_failure', status)

        @handler(None, None)
        def your_account(self, accdata):
            self.account = acc = Account.parse(accdata)
            self.event_cb('your_account', acc)

        @handler(None, None)
        def thbattle_greeting(self, ver):
            from settings import VERSION
            if ver != VERSION:
                self.event_cb('version_mismatch')
                Executive.disconnect()
            else:
                self.event_cb('server_connected', self)

        @handler(None, None)
        def ping(self, _):
            Executive.pong()

        @gevent.spawn
        def beater():
            while True:
                gevent.sleep(10)
                Executive.heartbeat()

        while True:
            cmd, data = Executive.server.ctlcmds.get()
            if cmd == 'shutdown':
                beater.kill()
                break

            h = handlers.get(cmd)
            if h:
                f, _from, _to = h
                if _from:
                    assert self.state in _from, 'Calling %s in %s state' % (f.__name__, self.state)
                if f: f(self, data)
                if _to: self.state = _to
            else:
                self.event_cb(cmd, data)


@instantiate
class Executive(object):
    def __init__(self):
        self.state = 'initial'  # initial connected

    def connect_server(self, addr, event_cb):
        if not self.state == 'initial':
            return 'server_already_connected'

        try:
            self.state = 'connecting'
            s = socket.create_connection(addr)
            self.server = svr = Server.spawn(s, addr)
            svr.link_exception(lambda *a: event_cb('server_dropped'))
            self.gamemgr = GameManager(event_cb)
            self.state = 'connected'
            self.gamemgr.start()
            return None

            # return 'server_connected'
        except Exception:
            self.state = 'initial'
            log.exception('Error connecting server')
            return 'server_connect_failed'

    def disconnect(self):
        if self.state != 'connected':
            return 'not_connected'
        else:
            self.state = 'dying'
            loop = gevent.getcurrent() is self.gamemgr

            @gevent.spawn
            def kill():
                self.server.kill()
                self.server.join()
                self.server.ctlcmds.put(['shutdown', None])
                self.gamemgr.join()
                self.server = self.gamemgr = None
                self.state = 'initial'

            if not loop:
                kill.join()

            return 'disconnected'

    def update(self, update_cb):
        from options import options
        import settings
        if options.no_update:
            return 'update_disabled'

        errord = [False]

        def do_update(name, path):
            up = Autoupdate(path)
            try:
                for p in up.update():
                    update_cb(name, p)
            except Exception as e:
                log.exception(e)
                errord[0] = True
                update_cb('error', e)

        pool = Pool(2)

        pool.spawn(do_update, 'logic_progress', settings.LOGIC_UPDATE_BASE)
        if settings.INTERPRETER_UPDATE_BASE:
            pool.spawn(do_update, 'interpreter_progress', settings.INTERPRETER_UPDATE_BASE)

        pool.join()

        return 'updated' if not errord[0] else 'error'

    def switch_version(self, version):
        import settings
        up = Autoupdate(settings.LOGIC_UPDATE_BASE)
        return up.switch(version)

    def is_version_match(self, version):
        import settings
        up = Autoupdate(settings.LOGIC_UPDATE_BASE)
        return up.is_version_match(version)

    def _simple_op(_type):
        def wrapper(self, *args):
            if not (self.state == 'connected'):
                return 'connect_first'

            self.server.write([_type, args])
        wrapper.__name__ = _type
        return wrapper

    auth             = _simple_op('auth')
    cancel_ready     = _simple_op('cancel_ready')
    change_location  = _simple_op('change_location')
    chat             = _simple_op('chat')
    create_game      = _simple_op('create_game')
    exit_game        = _simple_op('exit_game')
    get_lobbyinfo    = _simple_op('get_lobbyinfo')
    get_ready        = _simple_op('get_ready')
    heartbeat        = _simple_op('heartbeat')
    invite_grant     = _simple_op('invite_grant')
    invite_user      = _simple_op('invite_user')
    join_game        = _simple_op('join_game')
    kick_observer    = _simple_op('kick_observer')
    kick_user        = _simple_op('kick_user')
    observe_grant    = _simple_op('observe_grant')
    observe_user     = _simple_op('observe_user')
    pong             = _simple_op('pong')
    query_gameinfo   = _simple_op('query_gameinfo')
    quick_start_game = _simple_op('quick_start_game')
    set_game_param   = _simple_op('set_game_param')
    speaker          = _simple_op('speaker')

    del _simple_op
