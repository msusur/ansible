# This code is part of Ansible, but is an independent component.
# This particular file snippet, and this file snippet only, is BSD licensed.
# Modules you write using this snippet, which is embedded dynamically by Ansible
# still belong to the author of the module, and may assign their own license
# to the complete work.
#
# Copyright (c) 2015 Peter Sprygada, <psprygada@ansible.com>
#
# Redistribution and use in source and binary forms, with or without modification,
# are permitted provided that the following conditions are met:
#
#    * Redistributions of source code must retain the above copyright
#      notice, this list of conditions and the following disclaimer.
#    * Redistributions in binary form must reproduce the above copyright notice,
#      this list of conditions and the following disclaimer in the documentation
#      and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
# IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE
# USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#

import itertools

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.basic import env_fallback, get_exception
from ansible.module_utils.shell import Shell, ShellError, HAS_PARAMIKO

NET_TRANSPORT_ARGS = dict(
    host=dict(required=True),
    port=dict(type='int'),
    username=dict(fallback=(env_fallback, ['ANSIBLE_NET_USERNAME'])),
    password=dict(no_log=True, fallback=(env_fallback, ['ANSIBLE_NET_PASSWORD'])),
    ssh_keyfile=dict(fallback=(env_fallback, ['ANSIBLE_NET_SSH_KEYFILE']), type='path'),
    authorize=dict(default=False, fallback=(env_fallback, ['ANSIBLE_NET_AUTHORIZE']), type='bool'),
    auth_pass=dict(no_log=True, fallback=(env_fallback, ['ANSIBLE_NET_AUTH_PASS'])),
    provider=dict(type='dict'),
    transport=dict(choices=list()),
    timeout=dict(default=10, type='int')
)

NET_CONNECTION_ARGS = dict()

NET_CONNECTIONS = dict()


def to_list(val):
    if isinstance(val, (list, tuple)):
        return list(val)
    elif val is not None:
        return [val]
    else:
        return list()


class ModuleStub(object):
    def __init__(self, argument_spec, fail_json):
        self.params = dict()
        for key, value in argument_spec.items():
            self.params[key] = value.get('default')
        self.fail_json = fail_json

class Command(object):

    def __init__(self, command, output=None, prompt=None, response=None,
                 is_reboot=False, delay=0):

        self.command = command
        self.output = output
        self.prompt = prompt
        self.response = response
        self.is_reboot = is_reboot
        self.delay = delay

    def __str__(self):
        return self.command

class Cli(object):

    def __init__(self, connection):
        self.connection = connection
        self.default_output = connection.default_output or 'text'
        self.commands = list()

    def __call__(self, commands, output=None):
        commands = self.to_command(commands, output)
        return self.connection.run_commands(commands)

    def to_command(self, commands, output=None):
        output = output or self.default_output
        objects = list()
        for cmd in to_list(commands):
            if not isinstance(cmd, Command):
                cmd = Command(cmd, output)
            objects.append(cmd)
        return objects

    def add_commands(self, commands, output=None):
        commands = self.to_command(commands, output)
        self.commands.extend(commands)

    def run_commands(self):
        responses = self.connection.run_commands(self.commands)
        for resp, cmd in itertools.izip(responses, self.commands):
            cmd.response = resp
        return responses

class Config(object):

    def __init__(self, connection):
        self.connection = connection

    def invoke(self, method, *args, **kwargs):
        try:
            return method(*args, **kwargs)
        except AttributeError:
            exc = get_exception()
            raise NetworkError('undefined method "%s"' % method.__name__, exc=str(exc))
        except NotImplementedError:
            raise NetworkError('method not supported "%s"' % method.__name__)

    def __call__(self, commands):
        lines = to_list(commands)
        return self.invoke(self.connection.configure, commands)

    def load_config(self, commands, **kwargs):
        commands = to_list(commands)
        return self.invoke(self.connection.load_config, commands, **kwargs)

    def get_config(self, **kwargs):
        return self.invoke(self.connection.get_config, **kwargs)

    def commit_config(self, **kwargs):
        return self.invoke(self.connection.commit_config, **kwargs)

    def abort_config(self, **kwargs):
        return self.invoke(self.connection.abort_config, **kwargs)

    def save_config(self):
        return self.invoke(self.connection.save_config)


class NetworkError(Exception):

    def __init__(self, msg, **kwargs):
        super(NetworkError, self).__init__(msg)
        self.kwargs = kwargs


class NetworkModule(AnsibleModule):

    def __init__(self, *args, **kwargs):
        connect_on_load = kwargs.pop('connect_on_load', True)

        argument_spec = NET_TRANSPORT_ARGS.copy()
        argument_spec['transport']['choices'] = NET_CONNECTIONS.keys()
        argument_spec.update(NET_CONNECTION_ARGS.copy())

        if kwargs.get('argument_spec'):
            argument_spec.update(kwargs['argument_spec'])
        kwargs['argument_spec'] = argument_spec

        super(NetworkModule, self).__init__(*args, **kwargs)

        self.connection = None
        self._cli = None
        self._config = None

        try:
            transport = self.params['transport'] or '__default__'
            cls = NET_CONNECTIONS[transport]
            self.connection = cls()
        except KeyError:
            self.fail_json(msg='Unknown transport or no default transport specified')
        except (TypeError, NetworkError):
            exc = get_exception()
            self.fail_json(msg=exc.message)

        if connect_on_load:
            self.connect()

    @property
    def cli(self):
        if not self.connected:
            self.connect()
        if self._cli:
            return self._cli
        self._cli = Cli(self.connection)
        return self._cli

    @property
    def config(self):
        if not self.connected:
            self.connect()
        if self._config:
            return self._config
        self._config = Config(self.connection)
        return self._config

    @property
    def connected(self):
        return self.connection._connected

    def _load_params(self):
        super(NetworkModule, self)._load_params()
        provider = self.params.get('provider') or dict()
        for key, value in provider.items():
            for args in [NET_TRANSPORT_ARGS, NET_CONNECTION_ARGS]:
                if key in args:
                    if self.params.get(key) is None and value is not None:
                        self.params[key] = value

    def connect(self):
        try:
            if not self.connected:
                self.connection.connect(self.params)
                if self.params['authorize']:
                    self.connection.authorize(self.params)
        except NetworkError:
            exc = get_exception()
            self.fail_json(msg=exc.message)

    def disconnect(self):
        try:
            if self.connected:
                self.connection.disconnect()
        except NetworkError:
            exc = get_exception()
            self.fail_json(msg=exc.message)


class NetCli(object):
    """Basic paramiko-based ssh transport any NetworkModule can use."""

    def __init__(self):
        if not HAS_PARAMIKO:
            raise NetworkError(
                msg='paramiko is required but does not appear to be installed.  '
                'It can be installed using  `pip install paramiko`'
            )

        self.shell = None
        self._connected = False
        self.default_output = 'text'

    def connect(self, params, kickstart=True, **kwargs):
        host = params['host']
        port = params.get('port') or 22

        username = params['username']
        password = params.get('password')
        key_file = params.get('ssh_keyfile')
        timeout = params['timeout']

        try:
            self.shell = Shell(
                kickstart=kickstart,
                prompts_re=self.CLI_PROMPTS_RE,
                errors_re=self.CLI_ERRORS_RE,
            )
            self.shell.open(
                host, port=port, username=username, password=password,
                key_filename=key_file, timeout=timeout,
            )
        except ShellError:
            exc = get_exception()
            raise NetworkError(
                msg='failed to connect to %s:%s' % (host, port), exc=str(exc)
            )

    def disconnect(self, **kwargs):
        self._connected = False
        self.shell.close()

    def authorize(self, params, **kwargs):
        passwd = params['auth_pass']
        self.execute(Command('enable', prompt=self.NET_PASSWD_RE, response=passwd))

    def execute(self, commands, **kwargs):
        try:
            return self.shell.send(commands)
        except ShellError:
            exc = get_exception()
            raise NetworkError(exc.message, commands=commands)


def register_transport(transport, default=False):
    def register(cls):
        NET_CONNECTIONS[transport] = cls
        if default:
            NET_CONNECTIONS['__default__'] = cls
        return cls
    return register

def add_argument(key, value):
    NET_CONNECTION_ARGS[key] = value

def get_module(*args, **kwargs):
    # This is a temporary factory function to avoid break all modules
    # until the modules are updated.  This function *will* be removed
    # before 2.2 final
    return NetworkModule(*args, **kwargs)

