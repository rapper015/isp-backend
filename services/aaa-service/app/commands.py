"""Replaceable CoA/Disconnect adapter. pyrad performs packet cryptography."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from pyrad.client import Client, Timeout
from pyrad.dictionary import Dictionary
from pyrad.packet import CoAACK, CoANAK, DisconnectACK, DisconnectNAK, DisconnectRequest

@dataclass(frozen=True)
class CommandResult:
    status: str
    detail: str = ""

class RadiusCommandAdapter(ABC):
    @abstractmethod
    def send_disconnect(self, host: str, port: int, secret: str, attributes: dict[str, Any]) -> CommandResult: ...
    @abstractmethod
    def send_coa(self, host: str, port: int, secret: str, attributes: dict[str, Any]) -> CommandResult: ...
    @abstractmethod
    def test_connectivity(self, host: str, port: int, secret: str) -> CommandResult: ...

class DisabledRadiusCommandAdapter(RadiusCommandAdapter):
    """Safe default: commands require an explicitly wired production adapter."""
    def send_disconnect(self, host, port, secret, attributes): return CommandResult("FAILED", "RADIUS command adapter is not configured")
    def send_coa(self, host, port, secret, attributes): return CommandResult("FAILED", "RADIUS command adapter is not configured")
    def test_connectivity(self, host, port, secret): return CommandResult("FAILED", "RADIUS command adapter is not configured")

class PyradCommandAdapter(RadiusCommandAdapter):
    """RADIUS UDP command sender using pyrad's maintained packet implementation."""
    def __init__(self, timeout: float = 2.0, retries: int = 2):
        self.timeout, self.retries = timeout, retries
        self.dictionary = Dictionary(str(Path(__file__).with_name("radius.dictionary")))
    def _send(self, host: str, port: int, secret: str, attributes: dict[str, Any], disconnect: bool) -> CommandResult:
        try:
            client = Client(server=host, coaport=port, secret=secret.encode(), dict=self.dictionary, timeout=self.timeout, retries=self.retries)
            packet = client.CreateCoAPacket(code=DisconnectRequest) if disconnect else client.CreateCoAPacket()
            for key, value in attributes.items():
                if key in {"User-Name", "Acct-Session-Id", "NAS-IP-Address", "Framed-IP-Address", "Calling-Station-Id", "Filter-Id"}: packet[key] = value
            reply = client.SendPacket(packet)
            if reply.code in {DisconnectACK, CoAACK}: return CommandResult("ACKNOWLEDGED")
            if reply.code in {DisconnectNAK, CoANAK}: return CommandResult("NAK", "NAS rejected command")
            return CommandResult("FAILED", "unexpected RADIUS response")
        except Timeout: return CommandResult("TIMED_OUT", "NAS did not respond")
        except (OSError, ValueError): return CommandResult("FAILED", "RADIUS command delivery failed")
    def send_disconnect(self, host, port, secret, attributes): return self._send(host, port, secret, attributes, True)
    def send_coa(self, host, port, secret, attributes): return self._send(host, port, secret, attributes, False)
    def test_connectivity(self, host, port, secret): return CommandResult("FAILED", "CoA connectivity requires a session-targeted request")
