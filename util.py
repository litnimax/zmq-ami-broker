import json
import logging
import os
import StringIO
import uu
import uuid
import zmq

def get_logger(name, level='info', system_name=None):
    log_level = eval('logging.%s' % level.upper())
    logger = logging.getLogger(name)
    ch = logging.StreamHandler()
    ch.setLevel(log_level)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    logger.setLevel(log_level)
    return logger


class ZmqMessage(object):
    _data = {}
    uuid = None
    msg_type = None
    origin = None

    def __init__(self, origin=None, message=None):
        self.uuid = uuid.uuid4().hex
        self.origin = origin
        if message:
            self.load(message)

    def load(self, msg):
        msg = json.loads(msg)
        for k in msg.keys():
            self.__dict__[k] = msg[k]


    def _set_data(self):
        data = {
            'msg_type': self.msg_type,
            'uuid': self.uuid,
            'origin': self.origin
        }
        for k in [k for k in self.__dict__.keys() if k.startswith('x_')]:
            data[k] = self.__dict__[k]
        self._data = data
        return data

    def dump(self):
        return json.dumps(self._set_data())

    def json(self):
        return self._set_data()

    def pprint(self):
        data = self._set_data()
        result = {}
        for k in data.keys():
            result[k] = '%s...' % data[k][:100] if data[k] and len(data[k]) > 100 else data[k]
        return json.dumps(result, indent=2, sort_keys=True)


class FileMessage(ZmqMessage):
    x_file_name = None
    x_file_data = ''
    x_folder = None
    x_operation = None

    def __init__(self, origin=None, folder=None, file_name=None):
        super(FileMessage, self).__init__()
        self.origin = origin
        self.x_folder = folder
        self.x_file_name = file_name


    def load_file(self, file_path):
        out_file = StringIO.StringIO()
        uu.encode(file_path, out_file, self.x_file_name)
        self.x_file_data = out_file.getvalue()

    def load_data(self, data):
        in_file = StringIO.StringIO(data)
        out_file = StringIO.StringIO()
        uu.encode(in_file, out_file, self.x_file_name)
        self.x_file_data = out_file.getvalue()

    def dump_file(self):
        in_file = StringIO.StringIO(self.x_file_data)
        out_file = StringIO.StringIO()
        uu.decode(in_file, out_file)
        return out_file.getvalue()


    def process_operation(self):
        # Create folder if required
        if not os.path.isdir(self.x_folder):
            os.makedirs(self.x_folder)
        file_path = os.path.join(self.x_folder, self.x_file_name)
        if self.x_operation == 'PUT':
            open(file_path, 'w').write(self.dump_file())
        elif self.x_operation == 'DELETE':
            os.unlink(file_path)


class AsteriskConfig(FileMessage):
    msg_type = 'AsteriskConfig'


class AsteriskAction(ZmqMessage):
    msg_type = 'AsteriskAction'
    x_data = {}


class AsteriskActionStatus(ZmqMessage):
    msg_type = 'AsteriskActionStatus'

    def __init__(self, origin=None, data=None):
        super(AsteriskActionStatus, self).__init__()
        self.origin = origin
        self.x_data = data


class AsteriskEvent(ZmqMessage):
    msg_type = 'AsteriskEvent'

    def __init__(self, origin=None, data=None):
        super(AsteriskEvent, self).__init__()
        self.origin = origin
        self.x_data = data


class AgentPing(ZmqMessage):
    msg_type = 'AgentPing'

class AgentPong(ZmqMessage):
    msg_type = 'AgentPong'
