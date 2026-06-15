#!/usr/bin/env python3.7
# -*- coding: utf-8 -*-

from enum import IntEnum
import json


class MessageType(IntEnum):
    META = 0
    CONTROL = 1


class MetaAction(IntEnum):
    HELLO = 0
    BYE = 1


class ControlAction(IntEnum):
    ADD_CHAIN = 0
    DEL_CHAIN = 1
    ADD_PATH = 2
    DEL_PATH = 3


class IncompletePayload(Exception):
    pass


class ControllerMessage:
    def __init__(self, dpid, controller_ip):
        self._type = None
        self._action = None
        self._controller_ip = controller_ip
        self._dpid = dpid
        self.src = None
        self.dst = None
        self.chain_id = -1
        self.chain = []

    def set_type(self, t):
        if t in MessageType:
            self._type = t
            return True
        return False

    def set_action(self, action):
        if self._type == MessageType.META:
            if action in MetaAction:
                self._action = action
                return True
            return False
        elif self._type == MessageType.CONTROL:
            if action in ControlAction:
                self._action = action
                return True
            return False
        else:
            return False

    def pack(self):
        """
        Validate and return this message as a dict object
        """
        payload = {
            "Type": self._type,
            "Action": self._action,
            "POX IP": self._controller_ip,
            "DPID": self._dpid,
            "Source": self.src,
            "Destination": self.dst,
            "ChainID": self.chain_id,
            "Chain": self.chain
        }

        if self._type is None or self._action is None:
            raise IncompletePayload("Type and action cannot be None")
        if self._type == MessageType.CONTROL:
            if self._action == ControlAction.ADD_CHAIN:
                if len(self.chain) == 0:
                    raise IncompletePayload("Chain cannot be empty")
            if self._action in (ControlAction.ADD_PATH, ControlAction.DEL_PATH):
                if self.src is None or self.dst is None:
                    raise IncompletePayload("Missing source and destination")
            if self._action != ControlAction.DEL_PATH and self.chain_id == -1:
                raise IncompletePayload("Missing ChainID")

        return json.dumps(payload)


class ControllerMessageGenerator:
    def __init__(self, dpid, controller_ip):
        self._dpid = dpid
        self._controller_ip = controller_ip

    def new_message(self):
        return ControllerMessage(self._dpid, self._controller_ip)

    def new_meta_message(self):
        msg = self.new_message()
        msg.set_type(MessageType.META)
        return msg

    def new_control_message(self):
        msg = self.new_message()
        msg.set_type(MessageType.CONTROL)
        return msg

    def new_hello_message(self):
        msg = self.new_meta_message()
        msg.set_action(MetaAction.HELLO)
        return msg

    def new_bye_message(self):
        msg = self.new_meta_message()
        msg.set_action(MetaAction.BYE)
        return msg

    def new_addchain_message(self, chain_id, chain):
        msg = self.new_control_message()
        msg.set_action(ControlAction.ADD_CHAIN)
        msg.chain_id = chain_id
        msg.chain = chain
        return msg

    def new_delchain_message(self, chain_id):
        msg = self.new_control_message()
        msg.set_action(ControlAction.DEL_CHAIN)
        msg.chain_id = chain_id
        return msg

    def new_addpath_message(self, src, dst, chain_id):
        msg = self.new_control_message()
        msg.set_action(ControlAction.ADD_PATH)
        msg.src = src
        msg.dst = dst
        msg.chain_id = chain_id
        return msg

    def new_delpath_message(self, src, dst):
        msg = self.new_control_message()
        msg.set_action(ControlAction.DEL_PATH)
        msg.src = src
        msg.dst = dst
        return msg

