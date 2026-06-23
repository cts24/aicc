"""Unified Asterisk Manager Interface client."""
import asyncio
import logging
from typing import Optional

log = logging.getLogger(__name__)


class AMIClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self.reader = None
        self.writer = None
        self._connected = False
        self._lock = asyncio.Lock()

    async def connect(self):
        for attempt in range(3):
            try:
                self.reader, self.writer = await asyncio.wait_for(
                    asyncio.open_connection(self.cfg.ami_host, self.cfg.ami_port), timeout=5
                )
                await self.reader.readline()
                action_id = self._make_action_id()
                await self._send_action({
                    "Action": "Login", "ActionID": action_id,
                    "Username": self.cfg.ami_user, "Secret": self.cfg.ami_secret,
                })
                resp = await self._read_block_with_id(action_id)
                if resp.get("Response") == "Success":
                    self._connected = True
                    log.info(f"AMI connected ({self.cfg.agent_name})")
                    await self._drain_events()
                    return
                self._connected = False
                log.warning(f"AMI login failed: {resp}")
                return
            except Exception as e:
                log.warning(f"AMI connect attempt {attempt + 1} failed: {e}")
                self._connected = False
                if attempt < 2:
                    await asyncio.sleep(2)

    async def ensure_connected(self):
        if not self._connected:
            await self.connect()

    async def _drain_events(self):
        while True:
            try:
                await asyncio.wait_for(self.reader.readline(), timeout=0.3)
            except asyncio.TimeoutError:
                break

    def _make_action_id(self) -> str:
        import time
        return f"{self.cfg.agent_name.lower()}-{time.monotonic():.4f}"

    async def _send_action(self, action: dict):
        lines = "".join(f"{k}: {v}\r\n" for k, v in action.items()) + "\r\n"
        self.writer.write(lines.encode())
        await self.writer.drain()

    async def _read_block_with_id(self, action_id: str, timeout: float = 5.0) -> dict:
        import time
        deadline = time.monotonic() + timeout
        current = {}
        while time.monotonic() < deadline:
            try:
                remaining = deadline - time.monotonic()
                line = await asyncio.wait_for(self.reader.readline(), timeout=max(0.1, remaining))
            except asyncio.TimeoutError:
                break
            line = line.decode("utf-8", errors="ignore").strip()
            if not line:
                if current.get("ActionID") == action_id:
                    return current
                current = {}
                continue
            if ":" in line:
                key, _, val = line.partition(":")
                current[key.strip()] = val.strip()
        self._connected = False
        return {}

    async def get_agent_channel(self) -> Optional[str]:
        """Find Asterisk channel running AudioSocket on this agent's port."""
        await self.ensure_connected()
        if not self._connected:
            return None
        port_str = str(self.cfg.ami_channel_port or self.cfg.audiosocket_port)
        try:
            async with self._lock:
                import time
                action_id = self._make_action_id()
                await self._send_action({"Action": "Status", "ActionID": action_id})
                deadline = time.monotonic() + 5.0
                current = {}
                while time.monotonic() < deadline:
                    try:
                        line = await asyncio.wait_for(
                            self.reader.readline(), timeout=max(0.1, deadline - time.monotonic())
                        )
                    except asyncio.TimeoutError:
                        break
                    line = line.decode("utf-8", errors="ignore").strip()
                    if not line:
                        event = current.get("Event", "")
                        aid = current.get("ActionID", "")
                        if event == "StatusComplete" and aid == action_id:
                            break
                        if event == "Status" and aid == action_id:
                            appdata = (
                                current.get("ApplicationData", "") or
                                current.get("AppData", "") or
                                current.get("Appdata", "") or
                                current.get("Data", "")
                            )
                            if port_str in appdata:
                                found = current.get("Channel", "")
                                if found:
                                    log.info(f"AMI Status found {self.cfg.agent_name} channel: {found}")
                                    return found
                        current = {}
                        continue
                    if ":" in line:
                        key, _, val = line.partition(":")
                        current[key.strip()] = val.strip()
        except Exception as e:
            log.warning(f"AMI get_agent_channel failed: {e}")
        return None

    async def get_var(self, var_name: str) -> Optional[str]:
        await self.ensure_connected()
        if not self._connected:
            return None
        try:
            async with self._lock:
                action_id = self._make_action_id()
                await self._send_action({
                    "Action": "GetVar", "ActionID": action_id, "Variable": var_name,
                })
                resp = await self._read_block_with_id(action_id, timeout=5.0)
                val = resp.get("Value", "")
                return val if val else None
        except Exception as e:
            log.warning(f"AMI GetVar {var_name} failed: {e}")
            return None

    async def blind_transfer(self, channel: str, exten: str) -> bool:
        await self.ensure_connected()
        if not self._connected or not channel:
            return False
        try:
            async with self._lock:
                action_id = self._make_action_id()
                await self._send_action({
                    "Action": "Redirect", "ActionID": action_id,
                    "Channel": channel, "Context": self.cfg.transfer_context,
                    "Exten": exten, "Priority": "1",
                })
                resp = await self._read_block_with_id(action_id)
                success = resp.get("Response") == "Success"
                log.info(f"AMI Redirect {channel} → {exten}: {'OK' if success else resp}")
                return success
        except Exception as e:
            log.warning(f"AMI blind_transfer failed: {e}")
            return False

    async def originate_supervisor_check(self, caller_channel: str, ext_supervisor: str = "3000") -> Optional[str]:
        await self.ensure_connected()
        if not self._connected or not caller_channel:
            return None
        action_id = self._make_action_id()
        try:
            async with self._lock:
                await self._send_action({
                    "Action": "Originate", "ActionID": action_id,
                    "Channel": f"PJSIP/{ext_supervisor}",
                    "Context": "zara-supervisor", "Exten": "check",
                    "Priority": "1", "Timeout": "20000",
                    "Variable": f"BRIDGETO={caller_channel}",
                    "Async": "true",
                })
                resp = await self._read_block_with_id(action_id)
                if resp.get("Response") == "Success":
                    log.info(f"AMI Originate supervisor: {resp}")
                    return action_id
                log.warning(f"AMI Originate failed: {resp}")
        except Exception as e:
            log.warning(f"AMI originate_supervisor_check failed: {e}")
        return None

    async def wait_for_originate_response(self, action_id: str, timeout: float = 25.0) -> bool:
        try:
            import time
            async with self._lock:
                deadline = time.monotonic() + timeout
                current = {}
                while time.monotonic() < deadline:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        line = await asyncio.wait_for(
                            self.reader.readline(), timeout=min(remaining, 0.5)
                        )
                    except asyncio.TimeoutError:
                        continue
                    line = line.decode("utf-8", errors="ignore").strip()
                    if not line:
                        if (current.get("Event") == "OriginateResponse"
                                and current.get("ActionID", "") == action_id):
                            log.info(f"AMI OriginateResponse: reason={current.get('Reason','?')} text={current.get('ReasonTxt','?')}")
                            return True
                        current = {}
                    elif ":" in line:
                        k, _, v = line.partition(":")
                        current[k.strip()] = v.strip()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.warning(f"AMI wait_for_originate_response: {e}")
        return False

    async def close(self):
        if self.writer:
            try:
                self.writer.close()
            except Exception:
                pass


async def get_caller_id(cfg) -> str:
    try:
        reader, writer = await asyncio.open_connection(cfg.ami_host, cfg.ami_port)
        await reader.readline()
        writer.write(f"Action: Login\r\nUsername: {cfg.ami_user}\r\nSecret: {cfg.ami_secret}\r\nActionID: cid_login\r\n\r\n".encode())
        await writer.drain()
        for _ in range(10):
            line = await asyncio.wait_for(reader.readline(), timeout=2)
            if b"Response:" in line:
                break
        await asyncio.sleep(0.1)
        try:
            while True:
                await asyncio.wait_for(reader.readline(), timeout=0.2)
        except asyncio.TimeoutError:
            pass
        writer.write(f"Action: GetVar\r\nVariable: {cfg.ami_callerid_var}\r\nActionID: cid_getvar\r\n\r\n".encode())
        await writer.drain()
        caller_id = ""
        for _ in range(20):
            line = (await asyncio.wait_for(reader.readline(), timeout=2)).decode(errors="ignore").strip()
            if line.startswith("Value:"):
                caller_id = line.split(":", 1)[1].strip()
                break
        writer.close()
        return caller_id
    except Exception as e:
        log.warning(f"AMI get_caller_id failed: {e}")
        return ""
