"""
Zoom G3n/G3Xn/G5n MIDI SysEx communication.

Handles patch upload/download via the raw ALSA MIDI device.
Protocol based on zoom-zt2 project (github.com/mungewell/zoom-zt2)
and confirmed against a real G3Xn pedal.
"""
from __future__ import annotations

import binascii
import logging
import os
import struct
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO
from pathlib import Path

log = logging.getLogger("zoomdownloader.midi")


# ── Protocol constants ───────────────────────────────────────────────────────

SYSEX_START = 0xF0
SYSEX_END   = 0xF7
ZOOM_MFR_ID = 0x52

# Device IDs (byte after 0x52 0x00 in SysEx)
DEVICE_IDS = {
    "G1on":  0x63,
    "G3n":   0x6E,
    "G3Xn":  0x6E,
    "G5n":   0x6E,
}

# SysEx commands
CMD_IDENTITY_REQUEST = [SYSEX_START, 0x7E, 0x7F, 0x06, 0x01, SYSEX_END]
CMD_EDITOR_ON  = 0x50
CMD_EDITOR_OFF = 0x51
CMD_PC_MODE_ON  = 0x52
CMD_PC_MODE_OFF = 0x53
CMD_SEND_CURRENT_PATCH = 0x28
CMD_REQUEST_CURRENT_PATCH = 0x29
CMD_REQUEST_PATCH_BY_SLOT = 0x09
CMD_PATCH_CHECK = 0x44
CMD_PATCH_UPLOAD = 0x45
CMD_PATCH_DOWNLOAD = 0x46
CMD_PROGRAM_CHANGE = 0xC0

PTCF_SIZE = 736  # G3n/G3Xn/G5n patch data size
G3XN_MAX_EFFECTS = 7   # G3n/G3Xn firmware supports at most 7 simultaneous effects
EDTB_BYTES_PER_EFFECT = 24  # each EDTB entry is exactly 24 bytes


# ── G3Xn PTCF compatibility ─────────────────────────────────────────────────

def clamp_ptcf_effects_for_g3xn(ptcf_data: bytes) -> bytes:
    """Limit the effect count to G3XN_MAX_EFFECTS (7) for G3n/G3Xn compatibility.

    G5n patches can declare 8 or 9 effect slots; the G3n/G3Xn firmware only
    handles 7.  Writing a patch with more slots causes the firmware to reject
    the write (ACK error 0x03) or crash at pc_mode_off.

    The PTCF layout when n_effects > G3XN_MAX_EFFECTS:

      bytes  0–39   : fixed header
      bytes 40–N    : effect slot ID table, (n_effects - 1) × 4 bytes each
      bytes N+4 ..  : TXJ1 / TXE1 / EDTB / PPRM blocks

    G3Xn expects exactly (G3XN_MAX_EFFECTS - 1) = 6 table entries, with TXJ1
    starting at byte 64.  Extra table entries must be removed and the blocks
    that follow shifted left to fill the gap.

    Three fields are updated:
      • byte[12..16]  num_effects (LE32)        → G3XN_MAX_EFFECTS
      • effect table  (bytes 40..N)             → keep only first 6 entries
      • EDTB size                               → G3XN_MAX_EFFECTS × 24
    """
    if len(ptcf_data) < 16 or ptcf_data[:4] != b"PTCF":
        return ptcf_data  # not a recognised PTCF — pass through unchanged

    num_effects = struct.unpack_from("<I", ptcf_data, 12)[0]
    if num_effects <= G3XN_MAX_EFFECTS:
        return ptcf_data  # already compatible, nothing to do

    # Effect table: starts at byte 40, (num_effects - 1) * 4 bytes
    # G3Xn expects: (G3XN_MAX_EFFECTS - 1) * 4 bytes
    table_start = 40
    orig_entries = num_effects - 1
    want_entries = G3XN_MAX_EFFECTS - 1
    orig_table_end = table_start + orig_entries * 4
    want_table_end = table_start + want_entries * 4
    trim = orig_table_end - want_table_end   # bytes to remove

    log.info(
        "clamp_ptcf_effects_for_g3xn: clamping effect count %d → %d "
        "(G5n patch detected; removing %d excess effect table bytes)",
        num_effects, G3XN_MAX_EFFECTS, trim,
    )

    # Build new PTCF: header + truncated effect table + rest of blocks
    data = bytearray(ptcf_data[:want_table_end])    # header + 6 entries
    data.extend(ptcf_data[orig_table_end:])          # TXJ1 / TXE1 / EDTB / ...
    # Re-pad to original length so PTCF size stays 736
    if len(data) < len(ptcf_data):
        data.extend(b"\x00" * (len(ptcf_data) - len(data)))
    data = data[:len(ptcf_data)]

    # Update num_effects
    struct.pack_into("<I", data, 12, G3XN_MAX_EFFECTS)

    # Update the EDTB block size
    edtb_pos = bytes(data).find(b"EDTB")
    if edtb_pos >= 0:
        old_size = struct.unpack_from("<I", data, edtb_pos + 4)[0]
        new_size = G3XN_MAX_EFFECTS * EDTB_BYTES_PER_EFFECT
        log.debug(
            "clamp_ptcf_effects_for_g3xn: EDTB @%d  size %d → %d",
            edtb_pos, old_size, new_size,
        )
        struct.pack_into("<I", data, edtb_pos + 4, new_size)

    return bytes(data)


# ── 7-bit MIDI encoding/decoding ────────────────────────────────────────────
# MIDI SysEx can only carry 7-bit bytes (0x00–0x7F).
# Zoom encodes 8-bit data in groups of 7: a flags byte followed by 7 data bytes.
# Bit 6 of the flags byte is the MSB of data byte 0, bit 5 → byte 1, etc.

def encode_7bit(data: bytes) -> bytes:
    """Encode arbitrary 8-bit data into 7-bit MIDI-safe bytes."""
    result = bytearray()
    i = 0
    while i < len(data):
        chunk = data[i:i + 7]
        flags = 0
        encoded_bytes = bytearray()
        for bit, b in enumerate(chunk):
            if b & 0x80:
                flags |= 1 << (6 - bit)
            encoded_bytes.append(b & 0x7F)
        result.append(flags)
        result.extend(encoded_bytes)
        i += 7
    return bytes(result)


def decode_7bit(encoded: bytes) -> bytes:
    """Decode 7-bit MIDI data back to 8-bit."""
    result = bytearray()
    i = 0
    while i < len(encoded):
        flags = encoded[i]
        i += 1
        for bit in range(7):
            if i >= len(encoded):
                break
            b = encoded[i]
            if flags & (1 << (6 - bit)):
                b |= 0x80
            result.append(b)
            i += 1
    return bytes(result)


# ── Patch file parsing ──────────────────────────────────────────────────────

def _parse_tonelib_xml(xml_data: bytes, source: str = "<unknown>") -> bytes:
    """Parse ToneLib XML data and return PTCF bytes from the <data dump=...> attribute."""
    xml_data = xml_data.strip().rstrip(b"\x00")
    root = ET.fromstring(xml_data)
    data_el = root.find(".//data")
    if data_el is None:
        raise ValueError(f"No <data> element found in {source}")
    dump_str = data_el.get("dump", "")
    if not dump_str:
        raise ValueError(f"Empty dump attribute in {source}")
    return bytes(int(x, 16) for x in dump_str.split(","))


def parse_patch_file(path: str | Path) -> bytes:
    """
    Extract raw PTCF bytes from a .zg3xn / .zg3n / .zg5n file,
    or from a raw ToneLib.data XML file, or a bare PTCF binary.

    Supported formats:
      - Raw PTCF binary (starts with b"PTCF") — returned as-is
      - XML ToneLib.data — parsed directly
      - .zg* container: arbitrary header + embedded ZIP containing ToneLib.data
    """
    path = Path(path)
    raw = path.read_bytes()

    # Already a raw PTCF binary — return as-is
    if raw[:4] == b"PTCF":
        return raw

    # If the file looks like XML, parse directly
    stripped = raw.lstrip()
    if stripped[:1] == b"<" or stripped[:5] == b"<?xml":
        return _parse_tonelib_xml(raw, str(path))

    # .zg* format: locate the embedded ZIP by its magic bytes (PK\x03\x04)
    # rather than assuming a fixed header length.
    zip_offset = raw.find(b"PK\x03\x04")
    if zip_offset == -1:
        raise ValueError(
            f"No ZIP data found in {path} — unsupported .zg* format"
        )
    with zipfile.ZipFile(BytesIO(raw[zip_offset:])) as zf:
        xml_data = zf.read("ToneLib.data")

    return _parse_tonelib_xml(xml_data, str(path))


# ── MIDI device discovery ───────────────────────────────────────────────────

def find_midi_device() -> str | None:
    """
    Find the ALSA raw MIDI device path for a connected Zoom pedal.
    Returns e.g. '/dev/snd/midiC3D0' or None.
    """
    snd_dir = Path("/dev/snd")
    if not snd_dir.exists():
        return None

    for dev in sorted(snd_dir.iterdir()):
        if not dev.name.startswith("midi"):
            continue
        # Check if this MIDI device belongs to a Zoom pedal
        # Parse card number from midiCxDy
        try:
            card_num = dev.name[4:].split("D")[0].lstrip("C")
            card_dir = Path(f"/proc/asound/card{card_num}")
            id_file = card_dir / "id"
            if id_file.exists() and "Series" in id_file.read_text():
                return str(dev)
        except (ValueError, IndexError, OSError):
            continue
    return None


def find_amidi_port() -> str | None:
    """
    Find the ALSA hardware MIDI port (e.g. 'hw:3,0,0') for a Zoom pedal.
    Falls back to parsing amidi -l output.
    """
    import subprocess
    try:
        result = subprocess.run(
            ["amidi", "-l"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            if "ZOOM" in line.upper() or "G Series" in line:
                parts = line.split()
                for part in parts:
                    if part.startswith("hw:"):
                        return part
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


# ── Low-level MIDI I/O ──────────────────────────────────────────────────────

class ZoomDevice:
    """Communicate with a Zoom G3n/G3Xn/G5n pedal via ALSA raw MIDI."""

    def __init__(self, device_path: str | None = None, device_id: int = 0x6E,
                 debug: bool = False):
        self.device_id = device_id
        self.device_path = device_path or find_midi_device()
        self._fd: int | None = None
        if debug:
            log.setLevel(logging.DEBUG)
            # Ensure at least one handler if the browse logger hasn't set one up
            if not log.handlers and not log.parent.handlers:
                h = logging.StreamHandler()
                h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
                log.addHandler(h)

    def open(self) -> None:
        if self._fd is not None:
            return
        if not self.device_path:
            raise RuntimeError(
                "No Zoom MIDI device found. Is your pedal connected via USB?"
            )
        log.debug("MIDI open %s", self.device_path)
        self._fd = os.open(self.device_path, os.O_RDWR | os.O_NONBLOCK)
        self._drain()

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()

    def _drain(self) -> None:
        """Discard any stale bytes sitting in the RX buffer."""
        import select
        if self._fd is None:
            return
        drained = 0
        while True:
            rlist, _, _ = select.select([self._fd], [], [], 0)
            if not rlist:
                break
            chunk = os.read(self._fd, 4096)
            if not chunk:
                break
            drained += len(chunk)
        if drained:
            log.debug("_drain: discarded %d stale byte(s)", drained)

    def _write(self, data: bytes | bytearray | list) -> None:
        if self._fd is None:
            raise RuntimeError("Device not open")
        raw = bytes(data)
        log.debug("MIDI TX (%d bytes): %s", len(raw), raw.hex(" "))
        os.write(self._fd, raw)

    def _read(self, timeout: float = 2.0) -> bytes:
        """Read one complete SysEx message (F0 ... F7) with timeout."""
        import select
        if self._fd is None:
            raise RuntimeError("Device not open")

        buf = bytearray()
        deadline = _monotonic() + timeout

        while _monotonic() < deadline:
            remaining = max(0.01, deadline - _monotonic())
            rlist, _, _ = select.select([self._fd], [], [], remaining)
            if not rlist:
                continue
            chunk = os.read(self._fd, 4096)
            buf.extend(chunk)
            if SYSEX_END in buf:
                # Find the complete SysEx
                end_idx = buf.index(SYSEX_END)
                result = bytes(buf[:end_idx + 1])
                log.debug("MIDI RX (%d bytes): %s", len(result),
                          result[:64].hex(" ") + ("…" if len(result) > 64 else ""))
                return result

        log.warning("MIDI RX timeout (%.1fs) — got %d bytes: %s",
                    timeout, len(buf), buf[:64].hex(" ") if buf else "(empty)")
        return bytes(buf)

    # ── High-level commands ──────────────────────────────────────────────────

    def _sysex(self, *payload: int) -> bytes:
        """Build a SysEx message: F0 52 00 <device_id> <payload> F7."""
        return bytes([SYSEX_START, ZOOM_MFR_ID, 0x00, self.device_id]
                     + list(payload) + [SYSEX_END])

    def identify(self) -> bytes:
        """Send identity request, return response."""
        self._write(CMD_IDENTITY_REQUEST)
        return self._read(timeout=2.0)

    def editor_mode_on(self) -> bytes:
        """Enable editor mode (required before patch operations)."""
        self._write(self._sysex(CMD_EDITOR_ON))
        return self._read(timeout=1.0)

    def editor_mode_off(self) -> None:
        """Disable editor mode."""
        self._write(self._sysex(CMD_EDITOR_OFF))

    def select_patch(self, slot: int) -> None:
        """Send Program Change to switch to a patch slot (0-based)."""
        self._write(bytes([CMD_PROGRAM_CHANGE, slot & 0x7F]))

    def read_current_patch(self) -> bytes:
        """Request and return the current edit-buffer patch as raw PTCF."""
        self._write(self._sysex(CMD_REQUEST_CURRENT_PATCH))
        resp = self._read(timeout=3.0)
        # The pedal sometimes sends a short generic ACK (7 bytes) first and
        # then the actual patch data as a second SysEx message.  Read again.
        if len(resp) < 10:
            log.debug("read_current_patch: got short ACK, reading again")
            resp = self._read(timeout=3.0)
        if len(resp) < 10:
            raise RuntimeError(f"Short response reading current patch: {len(resp)} bytes")
        # Response: F0 52 00 6E 28 <encoded_data> F7
        payload = resp[5:-1]
        return decode_7bit(payload)

    def read_patch_slot(self, slot: int) -> bytes:
        """Read patch from a specific memory slot (0-based). Returns raw PTCF."""
        self._write(self._sysex(CMD_REQUEST_PATCH_BY_SLOT, 0x00, 0x00, slot & 0x7F))
        resp = self._read(timeout=3.0)
        if len(resp) < 10:
            raise RuntimeError(f"Short response reading slot {slot}: {len(resp)} bytes")
        # Response: F0 52 00 6E 08 00 00 <slot> <length_lo> <length_hi> <encoded_data> F7
        # PTCF data starts after decoding from offset 10
        payload = resp[10:-1]
        return decode_7bit(payload)

    def send_patch_to_current(self, ptcf_data: bytes) -> None:
        """
        Send PTCF data to the current edit buffer (volatile).
        This immediately loads the patch on the pedal but does NOT persist.
        """
        encoded = encode_7bit(ptcf_data)
        msg = self._sysex(CMD_SEND_CURRENT_PATCH, *encoded)
        self._write(msg)

    def pc_mode_on(self) -> bytes:
        """Enable PC mode (required for persistent patch read/write)."""
        # Normalise: send pc_mode_off first in case the pedal is already in PC
        # mode (e.g. from a previous failed session), then wait for its ACK so
        # it doesn't collide with the pc_mode_on response below.
        log.debug("pc_mode_on: normalising with CMD_PC_MODE_OFF (0x%02X)", CMD_PC_MODE_OFF)
        self._write(self._sysex(CMD_PC_MODE_OFF))
        self._read(timeout=0.3)   # consume ACK or silently time out
        self._drain()             # clear any residual bytes

        log.debug("pc_mode_on: sending CMD_PC_MODE_ON (0x%02X)", CMD_PC_MODE_ON)
        self._write(self._sysex(CMD_PC_MODE_ON))
        resp = self._read(timeout=2.0)
        if not resp:
            raise RuntimeError("pc_mode_on: no response from pedal (timeout)")
        if SYSEX_END not in resp:
            raise RuntimeError(
                f"pc_mode_on: incomplete response ({len(resp)} bytes)"
            )
        log.debug("pc_mode_on: OK (%d bytes)", len(resp))
        return resp

    def parse_current_slot(self, pc_resp: bytes) -> int | None:
        """
        Try to extract the currently-active slot from a pc_mode_on() response.

        The G3n/G3Xn PC-mode-ON SysEx reply embeds the memory layout (same
        fields as patch_check) followed by the current bank and location:
          d[4..5]  = count  (7-bit lo/hi)
          d[6..7]  = psize
          d[10..11]= bsize
          d[12..13]= current bank (lo/hi)
          d[14..15]= current location within bank (lo/hi)

        Returns the 0-based slot index, or None if the response is too short
        or the computed slot falls outside the valid range.
        """
        d = pc_resp[1:-1]  # strip F0 / F7
        log.debug("parse_current_slot: resp=%d bytes  hex=%s",
                  len(d), pc_resp[:24].hex(" "))
        if len(d) < 16:
            log.warning("parse_current_slot: response too short (%d bytes) — "
                        "cannot determine current slot", len(d))
            return None
        count = d[4] + d[5] * 128 or 150
        bsize = d[10] + d[11] * 128 or 3
        bank  = d[12] + d[13] * 128
        loc   = d[14] + d[15] * 128
        slot  = bank * bsize + loc
        if not (0 <= slot < count):
            log.warning("parse_current_slot: slot=%d out of range 0\u2013%d "
                        "(bank=%d loc=%d bsize=%d count=%d)",
                        slot, count - 1, bank, loc, bsize, count)
            return None
        log.info("parse_current_slot: slot=%d (bank=%d loc=%d bsize=%d)",
                 slot, bank, loc, bsize)
        return slot

    def _parse_slot_from_patch_dump(self, resp: bytes) -> int | None:
        """
        Extract the currently-active slot from a spontaneous CMD_PATCH_UPLOAD
        (0x45) state-dump packet the pedal emits when leaving PC mode.

        Packet layout (bytes, 0-indexed, including F0/F7 wrapper):
          [0]      F0
          [1..3]   52 00 6E      (Zoom manufacturer ID + device ID)
          [4]      0x45          (CMD_PATCH_UPLOAD)
          [5..6]   00 00         (reserved)
          [7]      bank_lo
          [8]      bank_hi
          [9]      loc_lo        (1-based location within bank)
          [10]     loc_hi
          [11..12] len_lo/hi     (PTCF length, e.g. 736 bytes → 0x60 0x05)
          [13+]    7-bit-encoded PTCF data
          [-1]     F7

        Returns the 0-based slot index, or None on failure.
        """
        if len(resp) < 13 or resp[4] != CMD_PATCH_UPLOAD:
            log.debug("_parse_slot_from_patch_dump: not a 0x45 packet (len=%d cmd=0x%02X)",
                      len(resp), resp[4] if len(resp) > 4 else 0)
            return None
        bank = resp[7] + resp[8] * 128
        loc  = resp[9] + resp[10] * 128   # 0-based within bank (same as write side)
        bsize = 3                          # G3n/G3Xn default
        slot = bank * bsize + loc
        if not (0 <= slot < 200):
            log.warning("_parse_slot_from_patch_dump: slot=%d out of range "
                        "(bank=%d loc=%d bsize=%d)", slot, bank, loc, bsize)
            return None
        log.info("_parse_slot_from_patch_dump: bank=%d loc=%d(0-based) bsize=%d → slot=%d",
                 bank, loc, bsize, slot)
        return slot

    def get_current_slot(self) -> int | None:
        """
        Query the currently-active slot by entering then immediately leaving PC
        mode.  Uses the exact same pc_mode_on()/pc_mode_off() pair as
        upload_patch(): when the pedal exits PC mode it always emits a
        CMD_PATCH_UPLOAD (0x45) state-dump packet that encodes the active bank
        and location.

        Returns the 0-based slot index, or None if it cannot be determined.
        """
        log.debug("get_current_slot: entering PC mode")
        self.pc_mode_on()
        dump = self.pc_mode_off()
        return self._parse_slot_from_patch_dump(dump)

    def pc_mode_off(self) -> bytes:
        """Disable PC mode and return the state-dump response from the pedal.

        When exiting PC mode the pedal validates and commits every modified
        slot before it responds.  This is proportional to the number and
        complexity of the patches just written — notably, patches containing
        effect module IDs that are unfamiliar to the firmware take noticeably
        longer to process.  Use a generous timeout so we never abandon the
        pedal mid-commit (which leaves it stuck in PC mode and requiring a
        factory reset).
        """
        log.debug("pc_mode_off")
        self._write(self._sysex(CMD_PC_MODE_OFF))
        resp = self._read(timeout=60.0)
        if not resp:
            raise RuntimeError(
                "pc_mode_off: pedal did not exit PC mode within 60 s — "
                "the pedal appears stuck. Power-cycle it to recover. "
                "If this keeps happening, one or more patches may contain "
                "effect modules that are not supported by your pedal firmware."
            )
        self._drain()
        return resp

    def patch_check(self) -> tuple[int, int, int]:
        """
        Query the pedal's patch memory layout.
        Returns (count, psize, bsize) where:
          - count: total number of patch slots
          - psize: patch data size in bytes
          - bsize: patches per bank
        """
        log.debug("patch_check: sending CMD_PATCH_CHECK (0x%02X)", CMD_PATCH_CHECK)
        self._write(self._sysex(CMD_PATCH_CHECK))
        resp = self._read(timeout=2.0)
        if not resp:
            raise RuntimeError("patch_check: no response from pedal (timeout)")
        # Response: F0 52 00 6E 43 <data...> F7
        # mido-style offsets (skip F0): [4]=count_lo, [5]=count_hi, etc.
        d = resp[1:-1]  # strip F0 and F7
        if len(d) < 12:
            # Device sent a short generic ACK instead of the detailed layout
            # response.  Fall back to known constants for G3n/G3Xn/G5n.
            count, psize, bsize = 150, PTCF_SIZE, 3
            log.warning(
                "patch_check: short response (%d bytes), using defaults "
                "count=%d psize=%d bsize=%d",
                len(resp), count, psize, bsize,
            )
            return count, psize, bsize
        count = d[4] + d[5] * 128
        psize = d[6] + d[7] * 128
        bsize = d[10] + d[11] * 128
        log.info("patch_check: count=%d psize=%d bsize=%d", count, psize, bsize)
        if bsize == 0:
            raise RuntimeError(
                f"patch_check: invalid bsize=0 (count={count} psize={psize})"
            )
        return count, psize, bsize

    def _slot_to_bank_loc(self, slot: int, bsize: int) -> tuple[int, int]:
        """Convert 0-based slot to (bank, loc) for the MIDI protocol."""
        location = slot + 1  # zoom-zt2 uses 1-based locations
        bank = (location - 1) // bsize
        loc = location - (bank * bsize) - 1
        return bank, loc

    def write_patch_to_slot(self, slot: int, ptcf_data: bytes, bsize: int) -> None:
        """
        Persistently write PTCF data to a memory slot.
        Uses command 0x45 with CRC32 checksum. Must be in PC mode.
        """
        bank, loc = self._slot_to_bank_loc(slot, bsize)
        length = len(ptcf_data)
        log.info("write_patch_to_slot: slot=%d → bank=%d loc=%d  length=%d",
                 slot, bank, loc, length)

        # Build packet body (without F0/F7)
        packet = bytearray([ZOOM_MFR_ID, 0x00, self.device_id, CMD_PATCH_UPLOAD, 0x00, 0x00])
        packet.append(bank & 0x7F)
        packet.append(bank >> 7)
        packet.append(loc & 0x7F)
        packet.append(loc >> 7)
        packet.append(length & 0x7F)
        packet.append((length >> 7) & 0x7F)

        packet.extend(encode_7bit(ptcf_data))

        # CRC32 checksum (inverted, split into 7-bit bytes)
        crc = binascii.crc32(ptcf_data) ^ 0xFFFFFFFF
        log.debug("write_patch_to_slot: CRC32=0x%08X  packet=%d bytes",
                  crc ^ 0xFFFFFFFF, len(packet) + 2)
        packet.append(crc & 0x7F)
        packet.append((crc >> 7) & 0x7F)
        packet.append((crc >> 14) & 0x7F)
        packet.append((crc >> 21) & 0x7F)
        packet.append((crc >> 28) & 0x0F)

        msg = bytes([SYSEX_START]) + bytes(packet) + bytes([SYSEX_END])
        self._write(msg)
        resp = self._read(timeout=5.0)  # wait for acknowledgment
        if not resp:
            raise RuntimeError(
                "write_patch_to_slot: no acknowledgment from pedal (timeout) — "
                "patch may not have been saved"
            )
        log.debug("write_patch_to_slot: ack received (%d bytes)", len(resp))

    def upload_patch(self, patch_path: str | Path, slot: int) -> str:
        """
        Upload a .zg3xn/.zg3n/.zg5n file to a pedal memory slot.

        Steps:
          1. Enter PC mode
          2. Query patch memory layout
          3. Write patch to slot with CRC32 checksum
          4. Exit PC mode

        Returns the patch name from the PTCF data.
        """
        log.info("upload_patch: file=%s slot=%d", patch_path, slot)
        ptcf_data = parse_patch_file(patch_path)
        log.debug("upload_patch: parsed PTCF — %d bytes, header: %s",
                  len(ptcf_data), ptcf_data[:8].hex(" "))

        name_bytes = ptcf_data[26:37]
        name = bytes(b for b in name_bytes if 0x20 <= b <= 0x7E).decode("ascii", errors="replace").strip()
        log.info("upload_patch: patch name = %r", name)

        self.pc_mode_on()
        try:
            _count, psize, bsize = self.patch_check()
            ptcf_len = len(ptcf_data)
            if ptcf_len < psize:
                ptcf_data = ptcf_data + b"\x00" * (psize - ptcf_len)
                log.debug("upload_patch: padded PTCF from %d to %d bytes", ptcf_len, psize)
            elif ptcf_len > psize:
                raise ValueError(
                    f"PTCF is {ptcf_len} bytes but pedal expects {psize} bytes "
                    f"\u2014 refusing to upload oversized patch data."
                )
            ptcf_data = clamp_ptcf_effects_for_g3xn(ptcf_data)
            self.write_patch_to_slot(slot, ptcf_data, bsize)
        finally:
            self.pc_mode_off()

        log.info("upload_patch: complete — '%s' → slot %d", name, slot)
        return name


def _monotonic() -> float:
    import time
    return time.monotonic()
