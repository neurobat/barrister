"""
    Barrister runtime for Python.  Includes all classes used when writing a client or server.

    :copyright: 2012 by James Cooper.
    :license: MIT, see LICENSE for more details.
"""
import urllib2
import uuid
import itertools
import logging
try:
    import json
except: 
    import simplejson as json

# JSON-RPC standard error codes
ERR_PARSE = -32700
ERR_INVALID_REQ = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL = -32603

# Our extensions
ERR_UNKNOWN = -32000
ERR_INVALID_RESP = -32001

def contract_from_file(fname):
    """
    Loads a Barrister IDL JSON from the given file and returns a Contract class

    :Parameters:
      fname
        Filename containing Barrister IDL JSON to load
    """
    f = open(fname)
    j = f.read()
    f.close()
    return Contract(json.loads(j))

def unpack_method(method):
    """
    Given a JSON-RPC method in:  [interface].[function] notation, returns a tuple of the interface
    name and function.

    For example, unpack_method("MyService.LoadUser") would return: ("MyService", "LoadUser")

    :Parameters:
      method
        String method name
    """
    pos = method.find(".")
    if pos == -1:
        raise RpcException(ERR_METHOD_NOT_FOUND, "Method not found: %s" % method)

    iface_name = method[:pos]
    func_name  = method[pos+1:]
    return iface_name, func_name

def idgen_uuid():
    """
    Generates a uuid4 (random) and returns the hex representation as a string
    """
    return uuid.uuid4().hex

idgen_seq_counter = itertools.count()
def idgen_seq():
    """
    Generates an ID using itertools.count() and returns it as a string
    """
    return str(idgen_seq_counter.next())

class RpcException(Exception, json.JSONEncoder):
    """
    Represents a JSON-RPC style exception.  Server implementations should raise this
    exception if they wish to communicate error codes back to Barrister clients.
    """

    def __init__(self, code, msg="", data=None):
        """
        Creates a new RpcException

        :Parameters:
          code
            Integer representing the error type. Applications may use any positive integer.
          msg
            Human readable description of the error
          data
            Optional extra info about the error. Should be a string, int, or list or dict of strings/ints
        """
        self.code = code
        self.msg = msg
        self.data = data

    def __str__(self):
        s = "RpcException: code=%d msg=%s" % (self.code, self.msg)
        if self.data:
            s += "%s data=%s" % (s, str(self.data))
        return s

class Server(object):
    """
    Dispatches requests to user created handler classes based on method name.
    Also responsible for validating requests and responses to ensure they conform to the
    IDL Contract.
    """

    def __init__(self, contract, validate_request=True, validate_response=True):
        """
        Creates a new Server

        :Parameters:
          contract
            Contract instance that this server should use
          validate_request
            If True, requests will be validated against the Contract and rejected if they are malformed
          validate_response
            If True, responses from handler methods will be validated against the Contract and rejected
            if they are malformed
        """
        logging.basicConfig()
        self.log = logging.getLogger("barrister")
        self.validate_req  = validate_request
        self.validate_resp = validate_response
        self.contract = contract
        self.handlers = { }

    def add_handler(self, iface_name, handler):
        """
        Associates the given handler with the interface name.  If the interface does not exist in
        the Contract, an RpcException is raised.

        :Parameters:
          iface_name
            Name of interface that this handler implements
          handler
            Instance of a class that implements all functions defined on the interface
        """
        if self.contract.has_interface(iface_name):
            self.handlers[iface_name] = handler
        else:
            raise RpcException(ERR_INVALID_REQ, "Unknown interface: '%s'", iface_name)

    def call_json(self, req_json):
        """
        Deserializes req_json as JSON, invokes self.call(), and serializes result to JSON.
        Returns JSON encoded string.

        :Parameters:
          req_json
            JSON-RPC request serialized as JSON string
        """
        try:
            req = json.loads(req_json)
        except:
            msg = "Unable to parse JSON: %s" % req_json
            return json.dumps(self._err(None, -32700, msg))
        return json.dumps(self.call(req))

    def call(self, req):
        """
        Executes a Barrister request and returns a response.  If the request is a list, then the
        response will also be a list.  If the request is an empty list, a RpcException is raised.

        :Parameters:
          req
            The request. Either a list of dicts, or a single dict.
        """
        resp = None

        if self.log.isEnabledFor(logging.DEBUG):
            self.log.debug("Request: %s" % str(req))

        if isinstance(req, list):
            if len(req) < 1:
                resp = self._err(None, ERR_INVALID_REQ, "Invalid Request. Empty batch.")
            else:
                resp = [ ]
                for r in req:
                    resp.append(self._call_and_format(r))
        else:
            resp = self._call_and_format(req)

        if self.log.isEnabledFor(logging.DEBUG):
            self.log.debug("Response: %s" % str(resp))
        return resp

    def _err(self, reqid, code, msg, data=None):
        """
        Formats a JSON-RPC error as a dict with keys: 'jsonrpc', 'id', 'error'
        """
        err = { "code": code, "message": msg }
        if data:
            err["data"] = data
        return { "jsonrpc": "2.0", "id": reqid, "error": err }
    
    def _call_and_format(self, req):
        """
        Invokes a single request against a handler using _call() and traps any errors,
        formatting them using _err().  If the request is successful it is wrapped in a 
        JSON-RPC 2.0 compliant dict with keys: 'jsonrpc', 'id', 'result'.

        :Parameters:
          req
            A single dict representing a single JSON-RPC request
        """
        if not isinstance(req, dict):
            return self._err(None, ERR_INVALID_REQ, 
                             "Invalid Request. %s is not an object." % str(req))

        reqid = None
        if req.has_key("id"):
            reqid = req["id"]

        try:
            resp = self._call(req)
            return { "jsonrpc": "2.0", "id": reqid, "result": resp }
        except RpcException as e:
            return self._err(reqid, e.code, e.msg, e.data)
        except:
            self.log.exception("Error processing request: %s" % str(req))
            return self._err(reqid, ERR_UNKNOWN, "Server error. Check logs for details.")
        

    def _call(self, req):
        """
        Executes a single request against a handler.  If the req.method == 'barrister-idl', the
        Contract IDL JSON structure is returned.  Otherwise the method is resolved to a handler
        based on the interface name, and the appropriate function is called on the handler.

        :Parameter:
          req
            A dict representing a valid JSON-RPC 2.0 request.  'method' must be provided.
        """
        if not req.has_key("method"):
            raise RpcException(ERR_INVALID_REQ, "Invalid Request. No 'method'.")

        method = req["method"]

        if method == "barrister-idl":
            return self.contract.idl_parsed

        iface_name, func_name = unpack_method(method)

        if self.handlers.has_key(iface_name):
            iface_impl = self.handlers[iface_name]
            func = getattr(iface_impl, func_name)
            if func:
                if req.has_key("params"):
                    params = req["params"]
                else:
                    params = [ ]

                if self.validate_req:
                    self.contract.validate_request(iface_name, func_name, params)

                if params:
                    result = func(*params)
                else:
                    result = func()

                if self.validate_resp:
                    self.contract.validate_response(iface_name, func_name, result)
                return result
            else:
                msg = "Method '%s' not found" % (method)
                raise RpcException(ERR_METHOD_NOT_FOUND, msg)
        else:
            msg = "No implementation of '%s' found" % (iface_name)
            raise RpcException(ERR_METHOD_NOT_FOUND, msg)        

class HttpTransport(object):
    """
    A client transport that uses urllib2 to make requests against a HTTP server.
    """

    def __init__(self, url, handlers=None, headers=None):
        """
        Creates a new HttpTransport

        :Parameters:
          url
            URL of the server endpoint
          handlers
            Optional list of handlers to pass to urllib2.build_opener()
          headers
            Optional list of HTTP headers to set on requests.  Note that Content-Type will always be set
            automatically to "application/json"
        """
        if not headers:
            headers = { }
        headers['Content-Type'] = 'application/json'
        self.url = url
        self.headers = headers
        if handlers:
            self.opener = urllib2.build_opener(*handlers)
        else:
            self.opener = urllib2.build_opener()
        
    def request(self, req):
        """
        Makes a request against the server and returns the deserialized result.

        :Parameters:
          req
            List or dict representing a JSON-RPC formatted request
        """
        data = json.dumps(req)
        req = urllib2.Request(self.url, data, self.headers)
        f = urllib2.urlopen(req)
        resp = f.read()
        f.close()
        return json.loads(resp)

class InProcTransport(object):
    """
    A client transport that invokes calls directly against a Server instance in process.
    This is useful for quickly unit testing services without having to go over the network.
    """
    def __init__(self, server):
        """ 
        Creates a new InProcTransport for the given Server

        :Parameters:
          server
            Barrister Server instance to bind this transport to
        """
        self.server = server

    def request(self, req):
        """
        Performs request against the given server.

        :Parameters:
          req
            List or dict representing a JSON-RPC formatted request
        """
        return self.server.call(req)

class Client(object):
    """
    Main class for consuming a server implementation.  Given a transport it loads the IDL from
    the server and creates proxy objects that can be called like local classes from your 
    application code.  

    With the exception of start_batch, you generally never need to use the methods provided by this
    class directly.

    For example:

    ::

      client = barrister.Client(barrister.HttpTransport("http://localhost:8080/OrderManagement"))
      status = client.OrderService.getOrderStatus("order-123")

    """

    def __init__(self, transport, validate_request=True, validate_response=True,
                 id_gen=idgen_uuid):
        """
        Creates a new Client for the given transport. When the constructor is called the
        client immediately makes a request to the server to load the IDL.  It then creates
        proxies for each interface in the IDL.  After constructing a client you can immediately
        begin making requests against the proxies.

        :Parameters:
          transport
            Transport object to use to make requests
          validate_request
            If True, the request will be validated against the Contract and a RpcException raised if 
            it does not match the IDL
          validate_response
            If True, the response will be validated against the Contract and a RpcException raised if 
            it does not match the IDL
          id_gen
            A callable to use to create request IDs.  JSON-RPC request IDs are only used by Barrister
            to correlate requests with responses when using a batch, but your application may use them
            for logging or other purposes.  UUIDs are used by default, but you can substitute another
            function if you prefer something shorter.
        """
        logging.basicConfig()
        self.log = logging.getLogger("barrister")
        self.transport = transport
        self.validate_req  = validate_request
        self.validate_resp = validate_response
        self.id_gen = id_gen
        req = {"jsonrpc": "2.0", "method": "barrister-idl", "id": "1"}
        resp = transport.request(req)
        self.contract = Contract(resp["result"])
        for k, v in self.contract.interfaces.items():
            setattr(self, k, InterfaceClientProxy(self, v))

    def call(self, iface_name, func_name, params):
        """
        Makes a single RPC request and returns the result.

        :Parameters:
          iface_name
            Interface name to call
          func_name
            Function to call on the interface
          params
            List of parameters to pass to the function
        """
        req  = self.to_request(iface_name, func_name, params)
        if self.log.isEnabledFor(logging.DEBUG):
            self.log.debug("Request: %s" % str(req))
        resp = self.transport.request(req)
        if self.log.isEnabledFor(logging.DEBUG):
            self.log.debug("Response: %s" % str(resp))
        return self.to_result(iface_name, func_name, resp)

    def to_request(self, iface_name, func_name, params):
        """
        Converts the arguments to a JSON-RPC request dict.  The 'id' field is populated
        using the id_gen function passed to the Client constructor.

        If validate_request==True on the Client constructor, the params are validated
        against the expected types for the function and a RpcException raised if they are
        invalid.

        :Parameters:
          iface_name
            Interface name to call
          func_name
            Function to call on the interface
          params
            List of parameters to pass to the function
        """
        if self.validate_req:
            self.contract.validate_request(iface_name, func_name, params)
            
        method = "%s.%s" % (iface_name, func_name)
        reqid = self.id_gen()
        return { "jsonrpc": "2.0", "id": reqid, "method": method, "params": params }

    def to_result(self, iface_name, func_name, resp):
        """
        Takes a JSON-RPC response and checks for an "error" slot. If it exists,
        a RpcException is raised.  If no "error" slot exists, the "result" slot is 
        returned.

        If validate_response==True on the Client constructor, the result is validated
        against the expected return type for the function and a RpcException raised if it is
        invalid.

        :Parameters:
          iface_name
            Interface name that was called
          func_name
            Function that was called on the interface
          resp
            Dict formatted as a JSON-RPC response
        """
        if resp.has_key("error"):
            e = resp["error"]
            data = None
            if e.has_key("data"):
                data = e["data"]
            raise RpcException(e["code"], e["message"], data)
            
        result = resp["result"]
        
        if self.validate_resp:
            self.contract.validate_response(iface_name, func_name, result)
        return result

    def start_batch(self):
        """
        Returns a new Batch object for the Client that can be used to make multiple RPC calls
        in a single request.
        """
        return Batch(self)

class InterfaceClientProxy(object):
    """
    Internal class used by the Client.  One instance is created per Client per interface found
    on the IDL returned from the server.
    """

    def __init__(self, client, iface):
        """
        Creates a new InterfaceClientProxy

        :Parameters:
          client
            Client instance to associate with this proxy
          iface
            Dict interface from the parsed IDL.  All functions defined on this interface will
            be defined on this proxy class as callables.
        """
        self.client = client
        iface_name = iface.name
        for func_name, func in iface.functions.items():
            setattr(self, func_name, self._caller(iface_name, func_name))

    def _caller(self, iface_name, func_name):
        """
        Returns a function for the given interface and function name.  When invoked it
        calls client.call() with the correct arguments.
        
        :Parameters:
          iface_name
            Name of interface to call when invoked
          func_name
            Name of function to call when invoked
          params
            Params pass to function from the calling application
        """
        def caller(*params):
            return self.client.call(iface_name, func_name, params)
        return caller        

class Batch(object):
    """
    Provides a way to batch requests together in a single call.  This class functions
    similiarly to the Client class.  InterfaceClientProxy instances are attached to the Batch
    instance, but when the application code calls them, the params are stored in memory until
    `batch.send()` is called.
    """

    def __init__(self, client):
        """
        Creates a new Batch for the given Client instance.  Rarely called directly.  Use
        client.start_batch() instead.

        :Parameters:
          client
            Client instance to associate with this Batch
        """
        self.client = client
        self.req_list = [ ]
        self.sent = False
        for k, v in client.contract.interfaces.items():
            setattr(self, k, InterfaceClientProxy(self, v))

    def call(self, iface_name, func_name, params):
        """
        Implements the call() function with same signature as Client.call().  Raises
        a RpcException if send() has already been called on this batch.  Otherwise
        appends the request to an internal list.

        This method is not commonly called directly.
        """
        if self.sent:
            raise Exception("Batch already sent. Cannot add more calls.")
        else:
            req = self.client.to_request(iface_name, func_name, params)
            self.req_list.append(req)

    def send(self):
        """
        Sends the batch request to the server and returns a BatchResult instance.
        Raises a RpcException if the batch has already been sent.
        """
        if self.sent:
            raise Exception("Batch already sent. Cannot send() again.")
        else:
            resp = self.client.transport.request(self.req_list)
            self.sent = True
            return BatchResult(self, self.req_list, resp)

class BatchResult(object):
    """
    Holds the results from a Batch.send() call.  Individual results are unmarshaled during each
    call to get()
    """

    def __init__(self, batch, req_list, resp):
        """
        Creates a new BatchResult, automatically reordering to the response to match the order of
        the requests.

        Raises RpcException if the resp length does not match the req_list length, or if any
        element in resp cannot be correlated to req_list based on id.

        Use the "count" property on the BatchResult instance to get the length of the response.

        :Parameters:
          batch
            Batch instance associated with this result
          req_list
            List of requests that were sent in this batch in (list of dicts in JSON-RPC request format)
          resp
            List of results from the server (lists of dicts in JSON-RPC response format)
        """
        if len(req_list) != len(resp):
            msg = "Batch response length %d != request %d" % (len(resp), len(req_list))
            raise RpcException(ERR_INVALID_RESP, msg)

        self.id_to_method = { }
        by_id = { }
        for r in resp:
            reqid = r["id"]
            by_id[reqid] = r

        in_req_order = [ ]
        for r in req_list:
            reqid = r["id"]
            if not by_id.has_key(reqid):
                msg = "Batch response missing result for request id: %s" % reqid
                raise RpcException(ERR_INVALID_RESP, msg)
            in_req_order.append(by_id[reqid])
            self.id_to_method[reqid] = r["method"]

        self.batch = batch
        self.resp = in_req_order
        self.count = len(in_req_order)

    def get(self, i):
        """
        Returns a single response from the BatchResult based on offset.  If the offset is out of range
        an IndexError is raised.  If the response contained an error, a RpcException is raised with the
        error information for that response.

        :Parameters:
          i 
            Offset into the result, zero based.
        """
        if i < self.count:
            resp = self.resp[i]
            method = self.id_to_method[resp["id"]]
            iface_name, func_name = unpack_method(method)
            return self.batch.client.to_result(iface_name, func_name, resp)
        else:
            raise IndexError("%d >= result size: %d" % (i, self.count))

class Contract(object):
    """
    Represents a single IDL file
    """

    def __init__(self, idl_parsed):
        """
        Creates a new Contract from the parsed IDL JSON

        :Parameters:
          idl_parsed
            Barrister parsed IDL as a list of dicts
        """
        self.idl_parsed = idl_parsed
        self.interfaces = { }
        self.structs = { }
        self.enums = { }
        for e in idl_parsed:
            if e["type"] == "struct":
                self.structs[e["name"]] = Struct(e, self)
            elif e["type"] == "enum":
                self.enums[e["name"]] = Enum(e)
            elif e["type"] == "interface":
                self.interfaces[e["name"]] = Interface(e, self)

    def validate_request(self, iface_name, func_name, params):
        """
        Validates that the given params match the expected length and types for this 
        interface and function.  

        Returns two element tuple: (bool, string)

        - `bool` - True if valid, False if not
        - `string` - Description of validation error, or None if valid

        :Parameters:
          iface_name
            Name of interface
          func_name
            Name of function
          params
            List of params to validate against this function
        """
        self.interface(iface_name).function(func_name).validate_params(params)

    def validate_response(self, iface_name, func_name, resp):
        """
        Validates that the response matches the return type for the function  

        Returns two element tuple: (bool, string)

        - `bool` - True if valid, False if not
        - `string` - Description of validation error, or None if valid

        :Parameters:
          iface_name
            Name of interface
          func_name
            Name of function
          resp
            Result from calling the function
        """
        self.interface(iface_name).function(func_name).validate_response(resp)

    def get(self, name):
        """
        Returns the struct, enum, or interface with the given name, or raises RpcException if
        no elements match that name.

        :Parameters:
          name
            Name of struct/enum/interface to return
        """
        if self.structs.has_key(name):
            return self.structs[name]
        elif self.enums.has_key(name):
            return self.enums[name]
        elif self.interfaces.has_key(name):
            return self.interfaces[name]
        else:
            raise RpcException(ERR_INVALID_PARAMS, "Unknown entity: '%s'" % name)

    def struct(self, struct_name):
        """
        Returns the struct with the given name, or raises RpcException if no struct matches
        """
        if self.structs.has_key(struct_name):
            return self.structs[struct_name]
        else:
            raise RpcException(ERR_INVALID_PARAMS, "Unknown struct: '%s'", struct_name)

    def has_interface(self, iface_name):
        """
        Returns True if an interface exists with the given name.  Otherwise returns False
        """
        return self.interfaces.has_key(iface_name)

    def interface(self, iface_name):
        """
        Returns the interface with the given name, or raises RpcException if no interface matches
        """
        if self.has_interface(iface_name):
            return self.interfaces[iface_name]
        else:
            raise RpcException(ERR_INVALID_PARAMS, "Unknown interface: '%s'", iface_name)

    def validate(self, expected_type, is_array, val):
        """
        Validates that the expected type matches the value

        Returns two element tuple: (bool, string)

        - `bool` - True if valid, False if not
        - `string` - Description of validation error, or None if valid

        :Parameters:
          expected_type
            string name of the type expected. This may be a Barrister primitive, or a user defined type.
          is_array
            If True then require that the val be a list
          val
            Value to validate against the expected type
        """
        if val == None:
            if expected_type.optional:
                return True, None
            else:
                return False, "Value cannot be null"
        elif is_array:
            if not isinstance(val, list):
                return self._type_err(val, "list")
            else:
                for v in val:
                    ok, msg = self.validate(expected_type, False, v)
                    if not ok:
                        return ok, msg
        elif expected_type.type == "int":
            if not isinstance(val, (long, int)):
                return self._type_err(val, "int")
        elif expected_type.type == "float":
            if not isinstance(val, (float, int, long)):
                return self._type_err(val, "float")
        elif expected_type.type == "bool":
            if not isinstance(val, bool):
                return self._type_err(val, "bool")
        elif expected_type.type == "string":
            if not isinstance(val, (str, unicode)):
                return self._type_err(val, "string")
        else:
            return self.get(expected_type.type).validate(val)
        return True, None

    def _type_err(self, val, expected):
        return False, "'%s' is of type %s, expected %s" % (val, type(val), expected)

class Interface(object):
    """
    Represents a Barrister IDL 'interface' entity.
    """

    def __init__(self, iface, contract):
        """
        Creates an Interface. Creates a 'functions' list of Function objects for
        each function defined on the interface.

        :Parameters:
          iface
            Dict representing the interface (from parsed IDL)
          contract
            Contract instance to associate the interface instance with
        """
        self.name = iface["name"]
        self.functions = { }
        for f in iface["functions"]:
            self.functions[f["name"]] = Function(self.name, f, contract)

    def function(self, func_name):
        """
        Returns the Function instance associated with the given func_name, or raises a
        RpcException if no function matches.
        """
        if self.functions.has_key(func_name):
            return self.functions[func_name]
        else:
            raise RpcException(ERR_METHOD_NOT_FOUND, 
                               "%s: Unknown function: '%s'", self.name, func_name)

class Enum(object):
    """
    Represents a Barrister IDL 'enum' entity.
    """

    def __init__(self, enum):
        """
        Creates an Enum.

        :Parameters:
          enum
            Dict representing the enum (from parsed IDL)
        """
        self.name = enum["name"]
        self.values = [ ]
        for v in enum["values"]:
            self.values.append(v["value"])

    def validate(self, val):
        """
        Validates that the val is in the list of values for this Enum.

        Returns two element tuple: (bool, string)

        - `bool` - True if valid, False if not
        - `string` - Description of validation error, or None if valid

        :Parameters:
          val
            Value to validate.  Should be a string.
        """
        if val in self.values:
            return True, None
        else:
            return False, "'%s' is not in enum: %s" % (val, str(self.values))

class Struct(object):
    """
    Represents a Barrister IDL 'struct' entity.
    """

    def __init__(self, s, contract):
        """
        Creates a Struct.

        :Parameters:
          s
            Dict representing the struct (from parsed IDL)
          contract
            Contract instance to associate with the Struct
        """
        self.contract = contract
        self.name = s["name"]
        self.extends = s["extends"]
        self.parent = None
        self.fields = { }
        for f in s["fields"]:
            self.fields[f["name"]] = Type(f)

    def field(self, name):
        """
        Returns the field on this struct with the given name. Will try to find this 
        name on all ancestors if this struct extends another.

        If found, returns a dict with keys: 'name', 'comment', 'type', 'is_array'
        If not found, returns None

        :Parameters:
          name
            string name of field to lookup
        """
        if self.fields.has_key(name):
            return self.fields[name]
        elif self.extends:
            if not self.parent:
                self.parent = self.contract.struct(self.extends)
            return self.parent.field(name)
        else:
            return None

    def validate(self, val):
        """
        Validates that the val matches the expected fields for this struct.
        val must be a dict, and must contain only fields represented by this struct and its
        ancestors.

        Returns two element tuple: (bool, string)

        - `bool` - True if valid, False if not
        - `string` - Description of validation error, or None if valid

        :Parameters:
          val
            Value to validate.  Must be a dict
        """
        if type(val) is not dict:
            return False, "%s is not a dict" % (str(val))

        for k, v in val.items():
            field = self.field(k)
            if field:
                ok, msg = self.contract.validate(field, field.is_array, v)
                if not ok:
                    return False, "field '%s': %s" % (field.name, msg)
            else:
                return False, "field '%s' not found in struct %s" % (k, self.name)

        for k, v in self.fields.items():
            if not val.has_key(k) and not v.optional:
                return False, "field '%s' missing from: %s" % (k, str(val))

        return True, None

class Function(object):
    """
    Represents a function defined on an Interface
    """

    def __init__(self, iface_name, f, contract):
        """
        Creates a new Function

        :Parameters:
          iface_name
            Name of interface this function belongs to
          f
            Dict from parsed IDL representing this function. keys: 'name', 'params', 'returns'
          contract
            Contract to associate this Function with
        """
        self.contract = contract
        self.name = f["name"]
        self.params = []
        for p in f["params"]:
            self.params.append(Type(p))
        self.returns = Type(f["returns"])
        self.full_name = "%s.%s" % (iface_name, self.name)
        
    def validate_params(self, params):
        """
        Validates params against expected types for this function.  
        Raises RpcException if the params are invalid.
        """
        plen = 0
        if params != None:
            plen = len(params)

        if len(self.params) != plen:
            vals = (self.full_name, len(self.params), plen)
            msg = "Function '%s' expects %d param(s). %d given." % vals
            raise RpcException(ERR_INVALID_PARAMS, msg)
        
        if params != None:
            i = 0
            for p in self.params:
                self._validate_param(p, params[i])
                i += 1

    def validate_response(self, resp):
        """
        Validates resp against expected return type for this function.  
        Raises RpcException if the response is invalid.
        """
        ok, msg = self.contract.validate(self.returns, 
                                         self.returns.is_array, resp)
        if not ok:
            vals = (self.full_name, str(resp), msg)
            msg = "Function '%s' invalid response: '%s'. %s" % vals
            raise RpcException(ERR_INVALID_RESP, msg)

    def _validate_param(self, expected, param):
        """
        Validates a single param against its expected type.
        Raises RpcException if the param is invalid
        
        :Parameters:
          expected
            Type instance
          param
            Parameter value to validate
        """
        ok, msg = self.contract.validate(expected, expected.is_array, param)
        if not ok:
            vals = (self.full_name, expected.name, msg)
            msg = "Function '%s' invalid param '%s'. %s" % vals
            raise RpcException(ERR_INVALID_PARAMS, msg)

class Type(object):

    def __init__(self, type_dict):
        self.name = ""
        self.optional = False
        if type_dict.has_key("name"):
            self.name = type_dict["name"]
        self.type = type_dict["type"]
        self.is_array = type_dict["is_array"]
        if type_dict.has_key("optional"):
            self.optional = type_dict["optional"]
