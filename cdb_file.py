"""
cdb_file.py – Parser and writer for Cyanide Studio .cdb save files.
"""

import os, struct, zlib, shutil
from collections import OrderedDict

# ─────────────────────────────────────────────────────────────────────────────
# Sentinel markers
# ─────────────────────────────────────────────────────────────────────────────

BLOCK_START = 0xAAAAAAAA
BLOCK_END   = 0xBBBBBBBB
FIELD_SEP   = 0xCCCCCCCC
SECT_SEP    = 0xDDDDDDDD

# Block type codes (second uint32 in block inner data)
TYPE_FILE_HDR   = 0x01
TYPE_TABLE_DEF  = 0x10
TYPE_FIELD_META = 0x11
TYPE_FIELD_DEF  = 0x20
TYPE_FIELD_DEF2 = 0x21
TYPE_DATA       = 0x22
TYPE_POOL       = 0x23
TYPE_FIELD_INFO = 0x24

# Field type specifiers
FTYPE_ID     = 0x15
FTYPE_STRING = 0x11
FTYPE_NAME   = 0x16
FTYPE_INT    = 0x12
FTYPE_REF    = 0x21
FTYPE_DATA   = 0x24


# ─────────────────────────────────────────────────────────────────────────────
# Low-level helpers
# ─────────────────────────────────────────────────────────────────────────────

def _u32(data, pos=0):
    return struct.unpack_from('<I', data, pos)[0]

def _f32(data, pos=0):
    return struct.unpack_from('<f', data, pos)[0]

def _build_pool_entries(pool_data: bytes) -> list:
    """Parse a null-terminated string pool into a list of strings (one per record)."""
    entries = []
    start = 0
    for j, b in enumerate(pool_data):
        if b == 0:
            entries.append(pool_data[start:j].decode('utf-8', errors='replace'))
            start = j + 1
    if start < len(pool_data) and pool_data[start:].strip(b'\x00'):
        entries.append(pool_data[start:].decode('utf-8', errors='replace'))
    return entries


# ─────────────────────────────────────────────────────────────────────────────
# CDBFile
# ─────────────────────────────────────────────────────────────────────────────

class CDBFile:
    """Parses a .cdb file and exposes tables/fields for reading and editing."""

    def __init__(self, filepath):
        self.filepath     = filepath
        self.raw_header   = None        # first 12 bytes of file
        self.decompressed = None        # bytearray of uncompressed data
        self.tables       = OrderedDict()  # table_name -> table dict
        self._load()

    # ── Loading ──────────────────────────────────────────────────────────────

    def _load(self):
        with open(self.filepath, 'rb') as f:
            raw = f.read()

        if _u32(raw, 0) != 0xFFFFFFFF:
            raise ValueError("Not a valid .cdb file (bad magic bytes)")

        self.raw_header   = raw[:12]
        self.decompressed = bytearray(zlib.decompress(raw[12:]))
        self._parse()

    def _parse(self):
        data   = self.decompressed
        n      = len(data)
        pos    = 0
        events = []

        # Build flat event stream
        while pos <= n - 4:
            v = _u32(data, pos)
            if v == BLOCK_START:
                end = pos + 4
                while end <= n - 4 and _u32(data, end) != BLOCK_END:
                    end += 4
                events.append({
                    'kind':       'BLOCK',
                    'inner':      bytes(data[pos + 4 : end]),
                    'suffix_pos': end + 4,
                    'suffix':     b'',
                })
                pos = end + 4
            elif v == FIELD_SEP:
                events.append({'kind': 'CC', 'inner': b'', 'suffix_pos': pos + 4, 'suffix': b''})
                pos += 4
            elif v == SECT_SEP:
                events.append({'kind': 'DD', 'inner': b'', 'suffix_pos': pos + 4, 'suffix': b''})
                pos += 4
            else:
                if events:
                    events[-1]['suffix'] += bytes(data[pos : pos + 4])
                pos += 4

        # Interpret event stream into tables
        current_table = None
        current_field = None

        for ev in events:
            if ev['kind'] != 'BLOCK':
                continue
            inner = ev['inner']
            if len(inner) < 8:
                continue

            v1 = _u32(inner, 4)

            if v1 == TYPE_TABLE_DEF and len(inner) >= 20:
                name_len = _u32(inner, 16)
                if 0 < name_len <= 128 and 20 + name_len <= len(inner):
                    raw_name = inner[20 : 20 + name_len].rstrip(b'\x00')
                    try:
                        table_name = raw_name.decode('ascii')
                    except Exception:
                        continue
                    if table_name.startswith('DYN_'):
                        current_table = table_name
                        current_field = None
                        if table_name not in self.tables:
                            self.tables[table_name] = {
                                'fields':       OrderedDict(),
                                'record_count': 0,
                            }

            elif v1 == TYPE_FIELD_DEF and len(inner) >= 24 and current_table:
                name_len = _u32(inner, 16)
                if 0 < name_len <= 128 and 20 + name_len <= len(inner):
                    raw_name = inner[20 : 20 + name_len].rstrip(b'\x00')
                    try:
                        fname = raw_name.decode('ascii').strip()
                    except Exception:
                        fname = ''
                    if fname:
                        current_field = fname
                        tbl = self.tables[current_table]
                        if fname not in tbl['fields']:
                            tbl['fields'][fname] = {
                                'values':       None,
                                'values_pos':   None,
                                'pool':         None,
                                'pool_entries': None,
                                'is_string':    False,
                                'is_float':     False,
                                'raw_count':    0,
                            }

            elif v1 == TYPE_DATA and current_table and current_field:
                suffix = ev['suffix']
                if suffix:
                    tbl = self.tables[current_table]
                    if current_field in tbl['fields']:
                        f = tbl['fields'][current_field]
                        f['values']     = suffix
                        f['values_pos'] = ev['suffix_pos']
                        f['raw_count']  = len(suffix) // 4
                        n_rec = len(suffix) // 4
                        if n_rec > tbl['record_count']:
                            tbl['record_count'] = n_rec
                        fn = current_field
                        f['is_string'] = False
                        f['is_float']  = (fn.startswith('gene_f_') or
                                          fn.startswith('value_f_') or
                                          fn.startswith('current_f_'))

            elif v1 == TYPE_POOL and current_table and current_field:
                suffix = ev['suffix']
                if len(suffix) > 4:
                    tbl = self.tables[current_table]
                    if current_field in tbl['fields']:
                        f = tbl['fields'][current_field]
                        pool_data         = suffix[4:]
                        f['pool']         = pool_data
                        f['pool_entries'] = _build_pool_entries(pool_data)
                        f['is_string']    = True

    # ── Public data access ────────────────────────────────────────────────────

    def get_record_count(self, table_name):
        if table_name not in self.tables:
            return 0
        return self.tables[table_name]['record_count']

    def get_field_names(self, table_name):
        if table_name not in self.tables:
            return []
        return list(self.tables[table_name]['fields'].keys())

    def get_value(self, table_name, field_name, record_idx):
        """Return the decoded value for one record in one field."""
        tbl = self.tables.get(table_name)
        if not tbl:
            return None
        f = tbl['fields'].get(field_name)
        if not f or f['values'] is None:
            return None
        pos = record_idx * 4
        if pos + 4 > len(f['values']):
            return None
        if f['is_string'] and f['pool_entries'] is not None:
            entries = f['pool_entries']
            return entries[record_idx] if record_idx < len(entries) else ''
        if f['is_float']:
            return _f32(f['values'], pos)
        return _u32(f['values'], pos)

    def get_column(self, table_name, field_name):
        """Return all values for a field as a list."""
        tbl = self.tables.get(table_name)
        if not tbl:
            return []
        f = tbl['fields'].get(field_name)
        if not f or f['values'] is None:
            return []
        n = f['raw_count']
        if f['is_string'] and f['pool_entries'] is not None:
            entries = f['pool_entries']
            return [entries[i] if i < len(entries) else '' for i in range(n)]
        if f['is_float']:
            return [_f32(f['values'], i * 4) for i in range(n)]
        return [_u32(f['values'], i * 4) for i in range(n)]

    def get_stat_byte(self, table_name, field_name, record_idx):
        """Read a byte-packed stat value (4 cyclists packed per uint32)."""
        tbl = self.tables.get(table_name)
        if not tbl:
            return 0
        f = tbl['fields'].get(field_name)
        if not f or f['values'] is None:
            return 0
        n_vals    = f['raw_count']
        n_records = tbl['record_count']
        if n_vals > 0 and n_records > 0 and n_records == n_vals * 4:
            pack_idx = record_idx // 4
            byte_off = record_idx %  4
            if pack_idx * 4 + 4 <= len(f['values']):
                pack = _u32(f['values'], pack_idx * 4)
                return (pack >> (byte_off * 8)) & 0xFF
        elif record_idx < n_vals:
            val = _f32(f['values'], record_idx * 4)
            return max(0, min(255, int(val * 255)))
        return 0

    def set_stat_byte(self, table_name, field_name, record_idx, new_byte_val):
        """Write a byte-packed stat value."""
        tbl = self.tables.get(table_name)
        if not tbl:
            return
        f = tbl['fields'].get(field_name)
        if not f or f['values'] is None:
            return
        new_byte_val = max(0, min(255, int(new_byte_val)))
        n_vals    = f['raw_count']
        n_records = tbl['record_count']
        buf = bytearray(f['values'])
        if n_records == n_vals * 4:
            pack_idx = record_idx // 4
            byte_off = record_idx %  4
            if pack_idx * 4 + 4 <= len(buf):
                pack = _u32(buf, pack_idx * 4)
                mask = ~(0xFF << (byte_off * 8)) & 0xFFFFFFFF
                pack = (pack & mask) | (new_byte_val << (byte_off * 8))
                struct.pack_into('<I', buf, pack_idx * 4, pack)
        elif record_idx < n_vals:
            struct.pack_into('<f', buf, record_idx * 4, new_byte_val / 255.0)
        f['values'] = bytes(buf)

    def set_value(self, table_name, field_name, record_idx, new_val):
        """Write a raw uint32 or float32 value."""
        tbl = self.tables.get(table_name)
        if not tbl:
            return
        f = tbl['fields'].get(field_name)
        if not f or f['values'] is None:
            return
        buf = bytearray(f['values'])
        pos = record_idx * 4
        if pos + 4 > len(buf):
            return
        if f['is_float'] or isinstance(new_val, float):
            struct.pack_into('<f', buf, pos, float(new_val))
        else:
            struct.pack_into('<I', buf, pos, int(new_val) & 0xFFFFFFFF)
        f['values'] = bytes(buf)

    # ── Saving ────────────────────────────────────────────────────────────────

    def save(self, filepath=None):
        """Write all changes back to disk. Creates a .bak backup first."""
        target = filepath or self.filepath

        if os.path.exists(target) and target == self.filepath:
            shutil.copy2(target, target + '.bak')

        data = bytearray(self.decompressed)
        for tbl in self.tables.values():
            for f in tbl['fields'].values():
                if f['values'] is not None and f['values_pos'] is not None:
                    pos = f['values_pos']
                    v   = f['values']
                    data[pos : pos + len(v)] = v

        compressed = zlib.compress(bytes(data), level=1)
        header = (struct.pack('<I', 0xFFFFFFFF) +
                  struct.pack('<I', len(data)) +
                  struct.pack('<I', len(compressed)))

        with open(target, 'wb') as out:
            out.write(header + compressed)
