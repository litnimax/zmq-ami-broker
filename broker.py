__author__ = 'litnimax@asteriskguru.ru'

import ConfigParser
import json
import logging
import os
import string
import sys
import time
import uuid
import zmq


class StateBroker:
    # Initial values
    device_state = False
    presence = False
    message_count = 0 # Here we count messages sent.
    presence_change_wait = 3 # We give 3 seconds to exchange presence states.
    servers = [] # Here we put servers from configuration file.
    log_level = logging.DEBUG # python logging level object.
    verbose_messages = False
    request_timeout = 1000 # Timeout in msec for network waiting.
                           # This is only needed if res_zmq_manager cmd event segfaulted.
    logger = logging.getLogger(__name__)
    # Here we keep sockets for operations
    # Create a context for all.
    context = zmq.Context()
    # AMI events poller
    event_poll = zmq.Poller()
    # .ini type  configuration
    config_filename = None
    config = ConfigParser.ConfigParser({'ami_trace': 'no'})
    # PresenceState messages are always sent back so we need a place to account it
    # {'
    presence_states = {}


    def __init__(self, filename=None):
        # Optional filename parameter
        if filename:
            self.config_filename = filename
            self.load_config(filename)


    def load_config(self, filename=None):
        config = self.config
        if filename:
            self.config_filename = filename
        self.logger.debug('Loading configuration from %s.' % self.config_filename)
        # Check if config can be opened
        try:
            # Test read
            open(self.config_filename).read()
        except (OSError, IOError), e:
            raise Exception('Cannot open config %s: %s' % (
                self.config_filename, e))
        # Read the config
        config.read(self.config_filename)
        # Config general settings
        self.device_state = config.getboolean('general', 'device_state')
        self.presence = config.getboolean('general', 'presence')
        self.log_level = eval('logging.%s' % config.get('general',
                                                        'log_level').upper())
        self.socket_timeout = config.get('general', 'request_timeout')
        self.count_messages = config.getboolean('general', 'count_messages')
        self.verbose_messages = config.getboolean('general', 'verbose_messages')
        # Config servers - strip list of servers.
        server_sections = map(string.strip, config.get('servers', 'sections').split(','))
        # Some magic here - just put all options in a dict.
        for section in server_sections:
            server = {'server_id': section}
            for k, v in config.items(section):
                server[k] = v
            # Add server to servers list.
            self.servers.append(server)

        # No init loggers after we have configuration
        self._init_logger()


    def start(self):
        self._connect_evt_sockets()
        self._connect_cmd_sockets()
        self._process_events()


    def _init_logger(self):
        # Just a logger # TODO: Add log to file
        ch = logging.StreamHandler()
        ch.setLevel(self.log_level)
        self.logger.addHandler(ch)
        self.logger.setLevel(self.log_level)


    def _connect_cmd_sockets(self):
        for server in self.servers:
            socket = self.context.socket(zmq.REQ)
            endpoint = 'tcp://%s:%s' % (server['addr'], server['cmd_port'])
            socket.connect(endpoint)
            self.logger.info('Connected to %s commands on %s' % (server['name'],
                                                        endpoint))
            # Assign cmd_socket
            server['cmd_socket'] = socket


    def _connect_evt_sockets(self):
        for server in self.servers:
            socket = self.context.socket(zmq.SUB)
            endpoint = 'tcp://%s:%s' % (server['addr'], server['evt_port'])
            socket.connect(endpoint)
            socket.setsockopt(zmq.SUBSCRIBE, b'{')
            self.logger.info('Connected to %s events on %s' % (server['name'],
                                                        endpoint))
            server['evt_socket'] = socket
            self.event_poll.register(socket, zmq.POLLIN)


    def _get_server_by_socket(self, socket):
        # Helper func to find server by socket
        for server in self.servers:
            if socket in [server['cmd_socket'], server['evt_socket']]:
                return server


    def _distribute_action(self, src_server, action):
        # Send to all servers except one that sent the event
        dst_servers = [s for s in self.servers if s['evt_socket'] != src_server['evt_socket']]
        self.logger.debug('Dst servers: %s' % ','.join(s['name'] for s in dst_servers))
        # Print nice actions
        if self.verbose_messages:
            self.logger.info('Distribute: %s' % json.dumps(action, indent=1))
        for dst_server in dst_servers:
            socket = dst_server['cmd_socket']
            poll = zmq.Poller()
            poll.register(socket, zmq.POLLIN)
            request_retries = 1 # Don't think it would be configured
            server = self._get_server_by_socket(socket)
            while request_retries > 0:
                self.logger.debug('Sending to %s: %s' % (server['name'], action))
                socket.send(json.dumps(action))
                socks = dict(poll.poll(self.request_timeout))
                if socks.get(socket) == zmq.POLLIN:
                    # Handle recv
                    reply = socket.recv()
                    self.logger.debug('Got reply: %s' % reply)
                    break
                else:
                    # Did not receive
                    self.logger.error('Did not receive REP from %s' %
                                      server['name'])
                    # As we did not recv reply get  rid from a dead REQ-REP socket
                    socket.setsockopt(zmq.LINGER, 0)
                    socket.close()
                    poll.unregister(socket)
                    request_retries -= 1
                    # Now reconnect the socket and try again
                    self.logger.debug('Reconnecting...')
                    socket = self.context.socket(zmq.REQ)
                    endpoint = 'tcp://%s:%s' % (server['addr'],
                                                server['cmd_port'])
                    socket.connect(endpoint)
                    server['cmd_socket'] = socket
                    self.logger.debug('Reconnected to %s and fixing REQ/REP socket state.' % endpoint)
                    poll.register(socket, zmq.POLLIN)


    def _process_events(self):
        while True:
            try:
                socks = dict(self.event_poll.poll(self.request_timeout))
                for sock in socks:
                    if not socks[sock] == zmq.POLLIN:
                        continue

                    try:
                        data = sock.recv()
                        message = json.loads(data)
                        # Find the server who owns the socket
                        src_server = self._get_server_by_socket(sock)
                        # Trace all AMI messages?
                        if self.config.getboolean(src_server['server_id'], 'ami_trace'):
                            self.logger.info('Got message from %s:\n%s.' % (
                                src_server['name'],
                                json.dumps(message, indent=4)
                                )
                            )
                        # Handle DeviceStateChange events
                        if message.get('Event') == 'DeviceStateChange' and self.device_state:
                            device = message['Device']
                            # We must distribute only generic channel states not custom
                            if device.startswith('Custom:'):
                                continue
                            # We must set DEVICE_STATE only on Custom devices
                            device = 'Custom:' + device
                            # Form AMI action
                            action = {
                                'Action': 'Setvar',
                                'ActionID': uuid.uuid4().hex,
                                'Variable': 'DEVICE_STATE(%s)' % device,
                                'Value': '%s' % message['State'],
                            }
                            self._distribute_action(src_server, action)

                        # Handle DevicePresenceChange events
                        elif message.get('Event') == 'PresenceStateChange' and self.presence:
                            # Now check if it is mirrored message
                            if self.presence_states.get(message['Presentity']):
                                # Yes we have it now let see if it is stale, we give 5 seconds for retranslate to complete
                                if time.time() - self.presence_states[message['Presentity']] < self.presence_change_wait:
                                    continue
                                else:
                                    self.presence_states.pop(message['Presentity'])

                            action = {
                                'Action': 'Setvar',
                                'ActionID': uuid.uuid4().hex,
                                'Variable': 'PRESENCE_STATE(%s)' %
                                            message.get('Presentity'),
                                'Value': '%s,%s' % (
                                    message.get('Status'),
                                    message.get('Subtype')
                                )
                            }
                            # Add current presence to tracking dictionary
                            self.presence_states[message.get('Presentity')] = time.time()
                            # Now send to other server
                            self._distribute_action(src_server, action)

                        else:
                            self.logger.debug('Ignoring event: %s' % message.get('Event'))

                    except ValueError:
                        self.logger.error(
                            'Unexpected message from %s receieved: %s' % (
                                src_server['name'], data)
                        )

            except KeyboardInterrupt:
                break



if __name__ == '__main__':
    if not len(sys.argv) == 2:
        print 'python broker.py config.ini'
        sys.exit(0)
    broker = StateBroker(filename=sys.argv[1])
    broker.start()