#!/usr/bin/env python2.7

__author__ = 'litnimax@asteriskguru.ru'

from multiprocessing import Process
from urlparse import urljoin
import requests
import sys
import zmq

import server_config as config
from util import *

context = zmq.Context()

def esb_server():
    try:
        logger = get_logger('esb_server', level=config.LOG_LEVEL)
        context = zmq.Context.instance()
        pub_sock = context.socket(zmq.PUB)
        #pub_sock.linger = 0
        pub_sock.setsockopt(zmq.TCP_KEEPALIVE, 1)
        pub_sock.bind(config.PUB_BIND_URL)
        sub_sock = context.socket(zmq.PULL)
        #sub_sock.linger = 0
        #sub_sock.setsockopt(zmq.SUBSCRIBE, '')
        sub_sock.bind(config.SUB_BIND_URL)
        logger.info('Started.')
        while True:
            target, msg = sub_sock.recv_multipart()
            zmq_msg = ZmqMessage()
            zmq_msg.load(msg)
            logger.info('Message %s from %s to %s, uuid %s.' % (zmq_msg.msg_type,
                                                            zmq_msg.origin, target,
                                                            zmq_msg.uuid))
            logger.debug('%s' % zmq_msg.pprint())
            pub_sock.send_multipart([target, msg])

    except KeyboardInterrupt:
        sub_sock.close()
        pub_sock.close()
        logger.info('ESB - exit.')


if __name__ == '__main__':
    try:
        p1 = Process(target=esb_server)
        p1.start()
        p1.join()
    except KeyboardInterrupt:
        sys.exit(0)
