# ØMQ Asterisk Manager Interface (AMI) Broker
ØMQ broker is used to distribute Asterisk AMI messges.
It is based on res_zmq_manager module from [here](https://github.com/litnimax/asterisk-zmq).

## How it works ##
![Images](https://raw.githubusercontent.com/litnimax/zmq-ami-broker/master/doc/asterisk-zeromq-state-dia.png)

The Broker connect to Asterisk res_manager_zmq module using ømq sockets. This asterisk module has 2 ømq sockets:
* addr_cmd - this socket is REQ/REP type and is used to accept AMI actions over ømq.
* addr_evt - this socket is SUB/PUB type and is used to pubish all AMI events to ømq endpoints, it does not accept actions.

So the Broker receives all AMI messages from all connected Asterisk servers and when events are 
DeviceStateChange or PresenceStateChange it passes these events to other servers using AMI action SetVar 
with DEVICE_STATE and PRESENCE_STATE functions.

## Requirements ##
* Python 2.7.
* System libs libzmq and libzmq-dev.
* Python extension pyZMQ (python-zmq system packages or installed via pip).
* Asterisk sources to compile res_zmq_manager.
* Asterisk modules res_manager_devicestate.so and res_manager_presencestate.so.
* enabled=yes in etc/asterisk/manager.conf

## Installation
Installation is shown in this screencast.
Installation steps:
* Download, compile, install and configure res_zmq_manager.
* Download Broker and add your servers to config.ini file (See [example_config.ini](/litnimax/zmq-ami-broker/blob/master/example_config.ini))
* Run the Broker

