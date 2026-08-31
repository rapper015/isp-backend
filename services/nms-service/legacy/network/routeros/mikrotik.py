import hashlib
import socket
import ssl

from .base import RouterAuthenticationError, RouterConnectionRefused, RouterPermissionError, RouterResponseError, RouterTLSError, RouterTimeout, RouterClient


def _length(value):
    if value<0x80:return bytes([value])
    if value<0x4000:return (value|0x8000).to_bytes(2,"big")
    if value<0x200000:return (value|0xC00000).to_bytes(3,"big")
    if value<0x10000000:return (value|0xE0000000).to_bytes(4,"big")
    return b"\xF0"+value.to_bytes(4,"big")


class MikroTikRouterClient(RouterClient):
    def __init__(self,host,port,username,password,use_ssl=True,verify_tls=True,timeout=5,ca_certificate=None,certificate_fingerprint=""):
        self.host=host;self.port=port;self.username=username;self.password=password;self.use_ssl=use_ssl;self.verify_tls=verify_tls;self.timeout=timeout;self.ca_certificate=ca_certificate;self.fingerprint=certificate_fingerprint.replace(":","").lower();self.sock=None

    def connect(self):
        try:
            sock=socket.create_connection((self.host,self.port),timeout=self.timeout);sock.settimeout(self.timeout)
            if self.use_ssl:
                context=ssl.create_default_context(cadata=self.ca_certificate or None)
                if not self.verify_tls: context.check_hostname=False;context.verify_mode=ssl.CERT_NONE
                sock=context.wrap_socket(sock,server_hostname=self.host if self.verify_tls else None)
                if self.fingerprint and hashlib.sha256(sock.getpeercert(binary_form=True)).hexdigest()!=self.fingerprint: raise RouterTLSError("Certificate fingerprint mismatch")
            self.sock=sock
            response=self._talk(["/login",f"=name={self.username}",f"=password={self.password}"])
            if response and response[-1].get("ret"):
                challenge=bytes.fromhex(response[-1]["ret"]); digest=b"\x00"+self.password.encode()+challenge
                response=self._talk(["/login",f"=name={self.username}",f"=response=00{hashlib.md5(digest).hexdigest()}"])
            if any(item.get("!type")=="!trap" for item in response): raise RouterAuthenticationError("RouterOS authentication failed")
        except socket.timeout as exc: self.close();raise RouterTimeout("Router connection timed out") from exc
        except ConnectionRefusedError as exc: self.close();raise RouterConnectionRefused("Router refused connection") from exc
        except ssl.SSLError as exc: self.close();raise RouterTLSError("Router TLS validation failed") from exc
        return self

    def close(self):
        if self.sock:
            try:self.sock.close()
            finally:self.sock=None

    def _read_length(self):
        first=self.sock.recv(1)
        if not first: raise RouterResponseError("Unexpected end of RouterOS response")
        value=first[0]
        if value<0x80:return value
        if value<0xC0:return ((value&0x3F)<<8)|self.sock.recv(1)[0]
        if value<0xE0:return ((value&0x1F)<<16)|int.from_bytes(self.sock.recv(2),"big")
        if value<0xF0:return ((value&0x0F)<<24)|int.from_bytes(self.sock.recv(3),"big")
        if value==0xF0:return int.from_bytes(self.sock.recv(4),"big")
        raise RouterResponseError("Invalid RouterOS word length")

    def _sentence(self):
        words=[]
        while True:
            size=self._read_length()
            if size==0:return words
            data=b""
            while len(data)<size:
                part=self.sock.recv(size-len(data))
                if not part:raise RouterResponseError("Truncated RouterOS response")
                data+=part
            words.append(data.decode("utf-8",errors="replace"))

    def _talk(self,words):
        for word in words:
            encoded=word.encode();self.sock.sendall(_length(len(encoded))+encoded)
        self.sock.sendall(b"\x00"); rows=[]
        while True:
            sentence=self._sentence()
            if not sentence:continue
            row={"!type":sentence[0]}
            for word in sentence[1:]:
                if word.startswith("="):
                    key,_,value=word[1:].partition("=");row[key]=value
            rows.append(row)
            if sentence[0] in ("!done","!fatal"):return rows

    def command(self,path,attributes=None,queries=None):
        words=[path]+[f"={k}={v}" for k,v in (attributes or {}).items()]+[f"?{k}={v}" for k,v in (queries or {}).items()]
        rows=self._talk(words)
        trap=next((row for row in rows if row["!type"] in ("!trap","!fatal")),None)
        if trap:
            message=trap.get("message","RouterOS command failed")
            if "permission" in message.casefold():raise RouterPermissionError(message)
            raise RouterResponseError(message)
        return [{k:v for k,v in row.items() if k!="!type"} for row in rows if row["!type"]=="!re"]
