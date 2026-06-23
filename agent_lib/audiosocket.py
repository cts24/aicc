"""AudioSocket protocol constants and helpers."""
import asyncio
import struct
import array

AS_HANGUP      = 0x00
AS_UUID        = 0x01
AS_AUDIO       = 0x10
AS_AUDIO_SLIN16 = 0x12
AS_ERROR       = 0xff


def pack_frame(kind: int, data: bytes) -> bytes:
    return struct.pack(">BH", kind, len(data)) + data


async def read_frame(reader: asyncio.StreamReader):
    hdr = await reader.readexactly(3)
    kind, length = struct.unpack(">BH", hdr)
    data = await reader.readexactly(length) if length else b""
    return kind, data


def downsample_16k_to_8k(pcm_bytes: bytes) -> bytes:
    """Downsample 16-bit PCM from 16kHz to 8kHz by decimation."""
    samples = array.array('h')
    samples.frombytes(pcm_bytes)
    return samples[::2].tobytes()
