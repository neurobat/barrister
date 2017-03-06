#!/usr/bin/env python

"""
    barrister
    ~~~~~~~~~

    A RPC toolkit for building lightweight reliable services.  Ideal for
    both static and dynamic languages.

    :copyright: (c) 2012 by James Cooper.
    :license: MIT, see LICENSE for more details.
"""

import uuid
import time
import unittest
import barrister
import six

def newUser(userId=u"abc123", email=None):
    return { "userId" : userId, "password" : u"pw", "email" : email,
      "emailVerified" : False, "dateCreated" : 1, "age" : 3.3 }

def now_millis():
    return int(time.time() * 1000)

class UserServiceImpl(object):

    def __init__(self):
        self.users = { }

    def get(self, userId):
        resp = self._resp(u"ok", u"user created")
        resp["user"] = self.users[userId]
        return resp

    def create(self, user):
        resp = self._resp(u"ok", u"user created")
        userId = six.text_type(uuid.uuid4().hex)
        user["dateCreated"] = now_millis()
        resp["userId"] = userId
        self.users[userId] = user
        return resp

    def update(self, user):
        userId = user["userId"]
        self.users[userId] = user
        return self._resp(u"ok", u"user updated")

    def validateEmail(self, userId):
        return self._resp(u"ok", u"user updated")

    def changePassword(self, userId, oldPass, newPass):
        return self._resp(u"ok", u"password updated")

    def countUsers(self):
        resp = self._resp(u"ok", u"ok")
        resp["count"] = len(self.users)
        return resp

    def getAll(self, userIds):
        return { "status": u"ok", "message" : u"users here", "users": [] }

    def _resp(self, status, message):
        return { "status" : status, "message" : message }

class RuntimeTest(unittest.TestCase):

    def setUp(self):
        contract = barrister.contract_from_file('./barrister/test/idl/runtime.json')
        self.user_svc = UserServiceImpl()
        self.server = barrister.Server(contract)
        self.server.add_handler("UserService", self.user_svc)

        transport = barrister.InProcTransport(self.server)
        self.client = barrister.Client(transport)

    def test_add_handler_invalid(self):
        self.assertRaises(barrister.RpcException, self.server.add_handler, "foo", self.user_svc)

    def test_user_crud(self):
        svc = self.client.UserService
        user = newUser(email=u"foo@example.com")
        del user["age"]  # field is optional
        resp = svc.create(user)
        self.assertTrue(resp["userId"])
        user2 = svc.get(resp["userId"])["user"]
        self.assertEqual(user["email"], user2["email"])
        self.assertTrue(user["dateCreated"] > 0)
        self.assertEqual(u"ok", svc.changePassword(u"123", u"oldpw", u"newpw")["status"])
        self.assertEqual(1, svc.countUsers()["count"])
        svc.getAll([])

    def test_invalid_req(self):
        svc = self.client.UserService
        cases = [
            [ svc.get ],  # too few args
            [ svc.get, 1, 2 ], # too many args
            [ svc.get, 1 ], # wrong type
            [ svc.create, None ], # wrong type
            [ svc.create, 1 ], # wrong type
            [ svc.create, { "UserId" : u"1" } ], # unknown param
            [ svc.create, { "userId" : 1 } ], # wrong type
            [ svc.getAll, { } ], # wrong type
            [ svc.getAll, [ 1 ] ] # wrong type
            ]
        for c in cases:
            try:
                if len(c) > 1:
                    c[0](c[1])
                else:
                    c[0]()
                self.fail("Expected RpcException for: %s" % str(c))
            except barrister.RpcException:
                pass

    def test_invalid_resp(self):
        svc = self.client.UserService
        responses = [
            { }, # missing fields
            { "status" : u"blah" }, # invalid enum
            { "status" : u"ok", "message" : 1 }, # invalid type
            { "status" : u"ok", "message" : u"good", "blarg" : True }, # invalid field
            { "status" : u"ok", "message" : u"good", "user" : { # missing password field
                    "userId" : u"123", "email" : u"foo@bar.com",
                    "emailVerified" : False, "dateCreated" : 1, "age" : 3.3 } },
            { "status" : u"ok", "message" : u"good", "user" : { # missing password field
                    "userId" : u"123", "email" : u"foo@bar.com",
                    "emailVerified" : False, "dateCreated" : 1, "age" : 3.3 } },
            { "status" : u"ok", "user" : { # missing message field
                    "userId" : u"123", "email" : u"foo@bar.com",
                    "emailVerified" : False, "dateCreated" : 1, "age" : 3.3 } }
            ]
        for resp in responses:
            self.user_svc.get = lambda id: resp
            try:
                svc.get(u"123")
                self.fail("Expected RpcException for response: %s" % str(resp))
            except barrister.RpcException:
                pass

    def test_batch(self):
        batch = self.client.start_batch()
        batch.UserService.create(newUser(userId=u"1", email=u"foo@bar.com"))
        batch.UserService.create(newUser(userId=u"2", email=u"foo@bar.com"))
        batch.UserService.countUsers()
        results = batch.send()
        self.assertEqual(3, len(results))
        self.assertEqual(results[0].result["message"], u"user created")
        self.assertEqual(results[1].result["message"], u"user created")
        self.assertEqual(2, results[2].result["count"])

    def _test_bench(self):
        start = time.time()
        stop = start+1
        num = 0
        while time.time() < stop:
            self.client.UserService.countUsers()
            num += 1
        elapsed = time.time() - start
        print("test_bench: num=%d microsec/op=%d" % (num, (elapsed*1000000)/num))

        start = time.time()
        stop = start+1
        num = 0
        while time.time() < stop:
            self.client.UserService.create(newUser())
            num += 1
        elapsed = time.time() - start
        print("test_bench: num=%d microsec/op=%d" % (num, (elapsed*1000000)/num))

if __name__ == "__main__":
    unittest.main()
