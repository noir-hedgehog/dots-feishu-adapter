"""HTTPS only, public DNS/IP pinning, normal CA + hostname verification."""
import http.client
import ipaddress
import json
import socket
import ssl
from urllib.parse import urlsplit

class SafeHTTPS:
    def __init__(self, allowed_hosts, resolver=socket.getaddrinfo, connector=socket.create_connection, tls=None):
        self.allowed_hosts=frozenset(allowed_hosts)
        if not self.allowed_hosts: raise ValueError('Explicit HTTPS hostname allowlist required')
        self.resolver,self.connector=resolver,connector
        self.tls=tls or ssl.create_default_context()
    def validate_url(self,url):
        p=urlsplit(url)
        if p.scheme!='https' or p.hostname not in self.allowed_hosts or p.username or p.password or p.fragment or p.port not in (None,443):
            raise ValueError('Destination rejected')
        if any(ord(x)<33 for x in url): raise ValueError('Destination rejected')
        return p
    def addresses(self,host):
        infos=self.resolver(host,443,type=socket.SOCK_STREAM)
        addresses=list(dict.fromkeys(i[4][0] for i in infos))
        if not addresses or any(not ipaddress.ip_address(x).is_global for x in addresses):
            raise ValueError('Non-public destination rejected')
        return addresses
    def request(self,url,method='GET',body=None,headers=None):
        p=self.validate_url(url)
        addresses=self.addresses(p.hostname) # revalidated on every attempt
        raw=None; conn=None
        try:
            # Connect to validated literal IP; never re-resolve hostname in connection.
            raw=self.connector((addresses[0],443),timeout=10)
            secured=self.tls.wrap_socket(raw,server_hostname=p.hostname)
            conn=http.client.HTTPConnection(p.hostname,443,timeout=10)
            conn.sock=secured
            target=(p.path or '/')+('?' + p.query if p.query else '')
            conn.request(method,target,body=body,headers=headers or {})
            response=conn.getresponse()
            if 300<=response.status<400: raise ValueError('Redirect rejected')
            data=response.read(262145)
            if len(data)>262144: raise ValueError('Response too large')
            return response.status,data
        finally:
            if conn: conn.close()
            elif raw: raw.close()
    def post(self,url,body,headers):
        if len(body)>262144: raise ValueError('Event too large')
        status,data=self.request(url,'POST',body,headers)
        try: payload=json.loads(data) if data else {}
        except (ValueError,UnicodeDecodeError): payload={}
        return status,payload
