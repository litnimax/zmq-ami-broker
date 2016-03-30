#!/usr/bin/env python2.7

__author__ = 'litnimax@asteriskguru.ru'

import sys
import time
from multiprocessing import Process
from urlparse import urljoin

import requests
import zmq

import agent_config as config
from util import *

logger = get_logger('asterisk_agent', level=config.LOG_LEVEL)

def ami_events_publisher():
    """
    The process connects to Asterisk ZMQ AMI events and sends
    some of them to ESB.
    """
    try:
        logger.info('Asterisk ZMQ pub started.')
        context = zmq.Context()
        # Connect to Asterisk events socket
        evt_socket = context.socket(zmq.SUB)
        #evt_socket.linger = 0
        evt_socket.setsockopt(zmq.SUBSCRIBE, '{')
        evt_socket.connect(config.ASTERISK_EVT_URL)
        # Connect to ESB PUB scoket
        pub_socket = context.socket(zmq.PUSH)
        #pub_socket.linger = 0
        pub_socket.connect(config.ZMQ_PUB_URL)
        while True:
            msg = evt_socket.recv_json()
            event = msg.get('Event', None)
            # DeviceStateChange from Asterisk
            if event == 'DeviceStateChange':
                if msg.get('State') == 'UNKNOWN':
                    # We do not replicate UNKNOWN states
                    continue
                logger.debug('My device %s state %s.' % (msg.get('Device'),
                                                      msg.get('State')))
                zmq_msg = AsteriskEvent(origin=config.SYSTEM_NAME,
                                        data={
                                            'Event': 'DeviceStateChange',
                                            'Device': msg.get('Device'),
                                            'State': msg.get('State')
                                        })
                pub_socket.send_multipart(['[*]', zmq_msg.dump()])
            # Reload event from Asterisk
            elif event == 'Reload':
                zmq_msg = AsteriskEvent(origin=config.SYSTEM_NAME,
                                        data={
                                            'Event': 'Reload',
                                            'Status': msg.get('Status'),
                                            'Module': msg.get('Module'),
                                       })
                # Send to all as we don't know who needs it.
                pub_socket.send_multipart(['[*]', zmq_msg.dump()])

            #else:
            #    print msg
    except KeyboardInterrupt:
        logger.info('AMI events - exit.')
        evt_socket.close()
        pub_socket.close()


# Asterisk commands
def asterisk_action(action):
    """
    :param action: {'Action': 'Name', ...}
    :return: reply from Asterisk
    """
    logger.debug('Sending action %s to %s' % (action.get('Action'),
                                              config.ASTERISK_CMD_URL))
    # Asterisk CMD socket
    context = zmq.Context.instance()
    sock = context.socket(zmq.REQ)
    sock.setsockopt(zmq.LINGER, 0)
    sock.connect(config.ASTERISK_CMD_URL)
    poll = zmq.Poller()
    try:
        poll.register(sock, zmq.POLLIN)
        sock.send(json.dumps(action))
        socks = dict(poll.poll(5000)) # 5 seconds to reply!
        if socks.get(sock) == zmq.POLLIN:
            reply = sock.recv_json()
            logger.debug('Asterisk reply: %s' % json.dumps(reply, indent=2,
                                                           sort_keys=True))
            return reply
        else:
            logger.error('Asterisk did not reply! Action: %s' % json.dumps(
                                            action, indent=2, sort_keys=True))

    except zmq.ZMQError, e:
        logger.error('Asterisk command ZMQError: %s' % e)

    finally:
        poll.unregister(sock)
        sock.close()




def subscriber():
    """
    This process listens and handles ESB events.
    """
    try:
        logger.info('Subscriber started.')
        context = zmq.Context()
        # Subscriber socket
        sub_socket = context.socket(zmq.SUB)
        #sub_socket.linger = 0
        sub_socket.setsockopt(zmq.SUBSCRIBE, '[*]')
        sub_socket.setsockopt(zmq.SUBSCRIBE, '[%s]' % config.SYSTEM_NAME)
        sub_socket.connect(config.ZMQ_SUB_URL)
        pub_socket = context.socket(zmq.PUSH)
        pub_socket.setsockopt(zmq.TCP_KEEPALIVE,1)
        #pub_socket.linger = 0
        pub_socket.connect(config.ZMQ_PUB_URL)

        # Process messages
        msg_counter = 0
        while True:
            target, msg = sub_socket.recv_multipart()
            msg_counter += 1
            # Log every 100 message count
            if msg_counter % 100 == 0:
                logger.info('Message counter: %s' % msg_counter)
            json_msg = json.loads(msg)
            msg_type = json_msg.get('msg_type')
            logger.debug('Subscriber message: %s' % json.dumps(json_msg,
                                                               indent=2,
                                                               sort_keys=True))

            if json_msg.get('origin') == config.SYSTEM_NAME:
                logger.debug('Ignoring as coming from myself...')
                continue

            # AgentPing
            if msg_type == 'AgentPing':
                origin = str(json_msg.get('origin'))
                pong = AgentPong(origin=config.SYSTEM_NAME)
                pub_socket.send_multipart(['[%s]' % str(origin), pong.dump()])
                logger.info('AsteriskPing received from %s, sending AsteriskPong' % origin)

            # DeviceStateChange
            elif msg_type == 'AsteriskEvent':
                data = json_msg.get('x_data', {})
                event, device, state = data.get('Event'), data.get('Device'), data.get('State')
                if event == 'DeviceStateChange' and not device.startswith('Custom:'):
                    action = {
                        'Action': 'SetVar',
                        'ActionID': json_msg.get('uuid'),
                        'Variable': 'DEVICE_STATE(Custom:%s)' % device,
                        'Value': '%s' % state,
                    }
                    asterisk_action(action)
                    logger.info('Other device: %s - %s' % (device, state))


            # AsteriskAction
            elif msg_type == 'AsteriskAction':
                action = AsteriskAction(message=msg)
                status = asterisk_action(action.x_data)
                logger.info('AsteriskAction: %s' % action.x_data)
                status_msg = AsteriskActionStatus(origin=config.SYSTEM_NAME,
                                              data=status)
                logger.info('AsteriskActionStatus: %s' % status)
                pub_socket.send_multipart(['[%s]' % str(action.origin), status_msg.dump()])


    except KeyboardInterrupt:
        logger.info('Subscriber: exit.')
        sub_socket.close()


def keep_alive():
    try:
        logger.info('Ping starter at interval %s' % config.KEEP_ALIVE_INTERVAL)
        context = zmq.Context.instance()
        pub_socket = context.socket(zmq.PUSH)
        # pub_socket.linger  = 0
        pub_socket.setsockopt(zmq.TCP_KEEPALIVE, 1)
        pub_socket.connect(config.ZMQ_PUB_URL)
        while True:
            # Ping Asterisk
            ping = {
                'Action': 'Ping'
            }
            reply = asterisk_action(ping)
            if reply and reply[0].get('Response'):
                status_msg = AsteriskActionStatus(origin=config.SYSTEM_NAME, data={
                    'Action': 'Ping',
                    'Response': reply[0].get('Response'),
                    'Timestamp': reply[0].get('Timestamp'),
                })
                pub_socket.send_multipart(['[AsteriskStats]', status_msg.dump()])
            else:
                logger.error('asterisk_ping unknown reply: %s' % reply)

            # Ping Subscriber for keep-alive
            ping = AgentPing(origin='%s' % config.SYSTEM_NAME)
            pub_socket.send_multipart(['[%s]' % str(config.SYSTEM_NAME),
                                       ping.dump()])
            logger.debug('AsteriskPing is sent.')

            time.sleep(config.KEEP_ALIVE_INTERVAL)

    except KeyboardInterrupt:
        logger.info('Ping: exit.')
        pub_socket.close()



if __name__ == '__main__':
    pid = str(os.getpid())
    pidfile = os.path.join(os.path.dirname(__file__), 'zmq_agent.pid')
    if os.path.isfile(pidfile):
        print "%s already exists, exiting." % pidfile
        sys.exit()
    open(pidfile, 'w').write(pid)
    try:
        p1 = Process(target=ami_events_publisher)
        p1.start()
        p2 = Process(target=subscriber)
        p2.start()
        p3 = Process(target=keep_alive)
        p3.start()
        # Wait for all
        p1.join()
        p2.join()
        p3.join()
    except KeyboardInterrupt:
        p1.terminate()
        p2.terminate()
        p3.terminate()
        logger.info('Main: exit.')
    finally:
        os.unlink(pidfile)

