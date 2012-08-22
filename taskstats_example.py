#!/usr/bin/python
#coding: utf-8

#from __future__ import absolute_import

from python_nl3.nl3.genl.socket     import Socket
from python_nl3.nl3.socket          import NL_CB_MSG_IN, NL_CB_CUSTOM, NL_OK, NL_STOP
from python_nl3.nl3.genl.message    import Message, c_msg_p
from python_nl3.nl3.genl.controller import CtrlCache
from python_nl3.nl3  import NL_AUTO_PORT, NL_AUTO_SEQ
from python_nl3.libc import FILE
from python_nl3      import taskstats

from ctypes import CFUNCTYPE, sizeof, POINTER, cast, c_int, c_void_p, c_char, byref
import select
import sys

import traceback

class Application(object):
    def __init__(self):
        self.outfile = None

        sock = Socket()
        sock.genl_connect()
        family = CtrlCache(sock).genl_ctrl_search_by_name(taskstats.TASKSTATS_GENL_NAME)
        #family_id = genl_ctrl_resolve(sock, taskstats.TASKSTATS_GENL_NAME)
        self.family_id = family.id_
        self.family_hdrsize = family.hdrsize

    def __callback(self, msg_p, _void_p):
        try:
            self._callback(Message(msg_p))
        except:
            traceback.print_exc() #print 'Exception in callback!', exc
            return NL_STOP
        else:
            return NL_OK

    def prepare_death_message(self):
        # multiprocessing.cpu_count() may be used for that, but we really need only online CPUS, anot not 0-{count}
        with open('/sys/devices/system/cpu/online', 'rt') as cpus_file:
            cpumask = cpus_file.read()

        msg = Message()
        msg.genlmsg_put(NL_AUTO_PORT, NL_AUTO_SEQ, self.family_id, 0, 0, taskstats.TASKSTATS_CMD_GET, taskstats.TASKSTATS_GENL_VERSION)
        msg.nla_put_string(taskstats.TASKSTATS_CMD_ATTR_REGISTER_CPUMASK, cpumask)
        return msg

    def do_poll(self):
        sock = Socket()
        sock.genl_connect()

        sock.nl_send_auto_complete(self.prepare_death_message())
        sock.nl_wait_for_ack()

        # http://www.infradead.org/~tgr/libnl/doc/core.html#core_sk_seq_num
        sock.nl_socket_disable_seq_check()

        cbtype = CFUNCTYPE(c_int, c_msg_p, c_void_p)

        callback = cbtype(self.__callback)

        sock.nl_socket_modify_cb(NL_CB_MSG_IN, NL_CB_CUSTOM, callback, None)

        # in order to able to interrupt process, we will poll() socket instead of blocking recv()
        # when python inside ctypes's function, SIGINT handling is suspended
        sock.nl_socket_set_nonblocking()
        poller = select.poll()
        poller.register(sock, select.POLLIN)
        while poller.poll():
            sock.nl_recvmsgs_default()

    def parse_inner(self, attr, length):
        length = c_int(length)

        if not attr.nla_ok(length):
            raise Exception('First inner attr is not OK')
        if attr.nla_type() != taskstats.TASKSTATS_TYPE_PID:
            raise Exception('First inner attribute is not TYPE_PID')

        print 'Dead pid is:', attr.nla_get_u32()

        attr = attr.nla_next(byref(length))
        if not attr.nla_ok(length):
            raise Exception('Second inner attribute is not OK')
        if attr.nla_type() != taskstats.TASKSTATS_TYPE_STATS:
            raise Exception('Second inner attribute is not TYPE_STATS')
        info = cast(attr.nla_data(), POINTER(taskstats.Taskstats_version_1)).contents
        info.dump()

        attr = attr.nla_next(byref(length))

        if length:
            raise Exception('Extra space in inner attributes', length)

    def parse_outer(self, attr, length):
        length = c_int(length)
        if not attr.nla_ok(length):
            raise Exception('Outer Attr is not OK')
        if attr.nla_type() != taskstats.TASKSTATS_TYPE_AGGR_PID:
            raise Exception('Nested (outer) attr is of invalid type')

        self.parse_inner(attr.nested_attr(), attr.nla_len())

        attr = attr.nla_next(byref(length))

        if length:
            raise Exception('Extra space after outer attr', length)


    def _callback(self, message):
        if self.outfile is None:
            self.outfile = FILE(sys.stdout)

        ghdr = message.nlmsg_hdr().genlmsg_hdr()

        self.parse_outer(ghdr.genlmsg_attrdata(self.family_hdrsize), ghdr.genlmsg_attrlen(self.family_hdrsize))

        print '-' * 80

def main():
    Application().do_poll()

if __name__ == '__main__':
    main()