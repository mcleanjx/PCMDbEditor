"""
PCM Database Editor
Reads, edits, and writes Cyanide Studio .cdb save files (Pro Cycling Manager).
"""

import os, struct, zlib, shutil, tkinter as tk
from tkinter import ttk, messagebox, filedialog
from collections import OrderedDict

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

BLOCK_START = 0xAAAAAAAA
BLOCK_END   = 0xBBBBBBBB
FIELD_SEP   = 0xCCCCCCCC
SECT_SEP    = 0xDDDDDDDD
MARKERS     = {BLOCK_START, BLOCK_END, FIELD_SEP, SECT_SEP}

# Block type codes (second uint32 in block inner data)
TYPE_FILE_HDR   = 0x01   # file header
TYPE_TABLE_DEF  = 0x10   # DYN_ table definition
TYPE_FIELD_META = 0x11   # field type metadata (string)
TYPE_FIELD_DEF  = 0x20   # field definition (has name)
TYPE_FIELD_DEF2 = 0x21   # secondary field definition
TYPE_DATA       = 0x22   # field data values
TYPE_POOL       = 0x23   # string / data pool
TYPE_FIELD_INFO = 0x24   # field info block

# Field type specifiers (from TYPE_FIELD_META blocks)
FTYPE_ID      = 0x15   # 21 – integer ID
FTYPE_STRING  = 0x11   # 17 – string (uses pool)
FTYPE_NAME    = 0x16   # 22 – name string (uses pool)
FTYPE_INT     = 0x12   # 18 – integer
FTYPE_REF     = 0x21   # 33 – reference/FK
FTYPE_DATA    = 0x24   # 36 – binary/data


# ─────────────────────────────────────────────────────────────────────────────
# CDB File Parser & Writer
# ─────────────────────────────────────────────────────────────────────────────

def _u32(data, pos=0):
    return struct.unpack_from('<I', data, pos)[0]

def _f32(data, pos=0):
    return struct.unpack_from('<f', data, pos)[0]


class CDBFile:
    """Parses a .cdb file and exposes tables/fields for reading and editing."""

    def __init__(self, filepath):
        self.filepath = filepath
        self.raw_header = None      # first 12 bytes of file
        self.decompressed = None    # bytearray of uncompressed data
        self.tables = OrderedDict() # table_name -> Table dict
        self._load()

    # ── Loading ──────────────────────────────────────────────────────────────

    def _load(self):
        with open(self.filepath, 'rb') as f:
            raw = f.read()

        magic        = _u32(raw, 0)
        uncomp_size  = _u32(raw, 4)
        comp_size    = _u32(raw, 8)

        if magic != 0xFFFFFFFF:
            raise ValueError("Not a valid .cdb file (bad magic bytes)")

        self.raw_header    = raw[:12]
        self.decompressed  = bytearray(zlib.decompress(raw[12:]))
        self._parse()

    def _parse(self):
        data   = self.decompressed
        n      = len(data)
        pos    = 0
        events = []   # list of dicts

        # ── Build flat event stream ──────────────────────────────────────────
        while pos <= n - 4:
            v = _u32(data, pos)
            if v == BLOCK_START:
                # find matching BLOCK_END
                end = pos + 4
                while end <= n - 4 and _u32(data, end) != BLOCK_END:
                    end += 4
                inner       = bytes(data[pos + 4 : end])
                suffix_pos  = end + 4
                events.append({
                    'kind':        'BLOCK',
                    'file_pos':    pos,
                    'inner':       inner,
                    'suffix_pos':  suffix_pos,   # byte offset in decompressed buf
                    'suffix':      b'',
                })
                pos = end + 4
            elif v == FIELD_SEP:
                events.append({'kind': 'CC', 'file_pos': pos, 'inner': b'',
                                'suffix_pos': pos + 4, 'suffix': b''})
                pos += 4
            elif v == SECT_SEP:
                events.append({'kind': 'DD', 'file_pos': pos, 'inner': b'',
                                'suffix_pos': pos + 4, 'suffix': b''})
                pos += 4
            else:
                # non-marker bytes → append to suffix of last event
                chunk = bytes(data[pos : pos + 4])
                if events:
                    events[-1]['suffix'] += chunk
                pos += 4

        # ── Interpret event stream into tables ───────────────────────────────
        current_table = None
        current_field = None

        for ev in events:
            if ev['kind'] != 'BLOCK':
                continue

            inner = ev['inner']
            if len(inner) < 8:
                continue

            v0 = _u32(inner, 0)
            v1 = _u32(inner, 4)

            # ── Table definition ─────────────────────────────────────────────
            if v1 == TYPE_TABLE_DEF and len(inner) >= 24:
                name_len = _u32(inner, 16) if len(inner) >= 20 else 0
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

            # ── Field definition (has name) ───────────────────────────────────
            elif v1 == TYPE_FIELD_DEF and len(inner) >= 20 and current_table:
                if len(inner) >= 24:
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
                                    'values':       None,   # raw bytes (one u32 per record)
                                    'values_pos':   None,   # byte offset in decompressed buf
                                    'pool':         None,   # string pool bytes
                                    'pool_entries': None,   # list of strings, one per record
                                    'is_string':    False,
                                    'is_float':     False,
                                    'raw_count':    0,      # len(values)//4
                                }

            # ── Data values block ─────────────────────────────────────────────
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
                        # Detect type from field name
                        fn = current_field
                        f['is_string'] = False
                        f['is_float']  = (fn.startswith('gene_f_') or
                                          fn.startswith('value_f_') or
                                          fn.startswith('current_f_'))

            # ── String / data pool block ──────────────────────────────────────
            elif v1 == TYPE_POOL and current_table and current_field:
                suffix = ev['suffix']
                if len(suffix) > 4:
                    tbl = self.tables[current_table]
                    if current_field in tbl['fields']:
                        f = tbl['fields'][current_field]
                        pool_data      = suffix[4:]   # skip 4-byte length header
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
        raw = _u32(f['values'], pos)
        if f['is_string'] and f['pool_entries'] is not None:
            entries = f['pool_entries']
            return entries[record_idx] if record_idx < len(entries) else ''
        if f['is_float']:
            return _f32(f['values'], pos)
        return raw

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
        """
        Read a byte-packed stat value.
        Fields with len//4 == record_count//4 pack 4 cyclist bytes per uint32.
        """
        tbl = self.tables.get(table_name)
        if not tbl:
            return 0
        f = tbl['fields'].get(field_name)
        if not f or f['values'] is None:
            return 0

        n_vals    = f['raw_count']
        n_records = tbl['record_count']

        if n_vals > 0 and n_records > 0 and n_records == n_vals * 4:
            # 4 bytes packed per uint32
            pack_idx  = record_idx // 4
            byte_off  = record_idx %  4
            if pack_idx * 4 + 4 <= len(f['values']):
                pack = _u32(f['values'], pack_idx * 4)
                return (pack >> (byte_off * 8)) & 0xFF
        elif record_idx < n_vals:
            # One float per record, map to 0-255
            val = _f32(f['values'], record_idx * 4)
            return max(0, min(255, int(val * 255)))
        return 0

    def set_stat_byte(self, table_name, field_name, record_idx, new_byte_val):
        """Write a byte-packed stat value and mark the field dirty."""
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

        # Backup
        if os.path.exists(target) and target == self.filepath:
            shutil.copy2(target, target + '.bak')

        # Apply all modified field values into a fresh copy of decompressed data
        data = bytearray(self.decompressed)
        for tbl in self.tables.values():
            for f in tbl['fields'].values():
                if f['values'] is not None and f['values_pos'] is not None:
                    pos = f['values_pos']
                    v   = f['values']
                    data[pos : pos + len(v)] = v

        # Re-compress and write
        compressed = zlib.compress(bytes(data), level=1)
        header = (struct.pack('<I', 0xFFFFFFFF) +
                  struct.pack('<I', len(data)) +
                  struct.pack('<I', len(compressed)))

        with open(target, 'wb') as out:
            out.write(header + compressed)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

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


def _fmt_val(v) -> str:
    if isinstance(v, float):
        return f'{v:.3f}'
    if v is None:
        return ''
    return str(v)[:40]


def _bdate_str(v: int) -> str:
    s = str(v)
    if len(s) == 8:
        return f'{s[:4]}-{s[4:6]}-{s[6:]}'
    return s


# ─────────────────────────────────────────────────────────────────────────────
# GUI – PCM Editor Application
# ─────────────────────────────────────────────────────────────────────────────

CYCLIST_STATS = [
    ('Flat',           'charac_i_plain',           'limit_i_plain'),
    ('Mountain',       'charac_i_mountain',         'limit_i_mountain'),
    ('Med. Mountain',  'charac_i_medium_mountain',  'limit_i_medium_mountain'),
    ('Downhill',       'charac_i_downhilling',      'limit_i_downhilling'),
    ('Cobbles',        'charac_i_cobble',            'limit_i_cobble'),
    ('Time Trial',     'charac_i_timetrial',         'limit_i_timetrial'),
    ('Prologue',       'charac_i_prologue',          'limit_i_prologue'),
    ('Sprint',         'charac_i_sprint',            'limit_i_sprint'),
    ('Acceleration',   'charac_i_acceleration',      'limit_i_acceleration'),
    ('Endurance',      'charac_i_endurance',         'limit_i_endurance'),
    ('Resistance',     'charac_i_resistance',        'limit_i_resistance'),
    ('Recovery',       'charac_i_recuperation',      'limit_i_recuperation'),
    ('Hill',           'charac_i_hill',              'limit_i_hill'),
    ('Baroudeur',      'charac_i_baroudeur',         'limit_i_baroudeur'),
]

RIDER_TYPES = {
    0: 'Unknown', 1: 'All-rounder', 2: 'Climber', 3: 'Sprinter',
    4: 'Time Trialist', 5: 'Classics', 6: 'Puncheur', 7: 'Sprinter',
}

# Dark-theme colour palette
BG       = '#1a1a2e'
BG_MID   = '#16213e'
BG_CARD  = '#0f3460'
ACCENT   = '#e94560'
FG       = '#eaeaea'
FG_DIM   = '#888899'
BAR_BG   = '#2d2d4e'
BAR_POT  = '#1a4a5a'


def _bar_color(value: int) -> str:
    """Return a hex color for a stat bar from 0-100."""
    t = value / 100.0
    r = int(220 * (1 - t) + 60 * t)
    g = int(80  * (1 - t) + 200 * t)
    b = int(60)
    return f'#{r:02x}{g:02x}{b:02x}'


class PCMEditorApp:

    def __init__(self, root: tk.Tk):
        self.root       = root
        self.db: CDBFile | None = None
        self._changes   = {}       # {(table, field, idx): new_val}
        self._cyclist_idx: int | None = None
        self._search_results: list  = []

        self.root.title('PCM Database Editor')
        self.root.geometry('1280x820')
        self.root.configure(bg=BG_MID)

        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        self._build_menu()
        self._build_toolbar()
        self._build_notebook()
        self._build_statusbar()

    def _build_menu(self):
        mb = tk.Menu(self.root)
        fm = tk.Menu(mb, tearoff=0)
        fm.add_command(label='Open…',   command=self.cmd_open,    accelerator='Ctrl+O')
        fm.add_command(label='Save',    command=self.cmd_save,    accelerator='Ctrl+S')
        fm.add_command(label='Save As…',command=self.cmd_save_as)
        fm.add_separator()
        fm.add_command(label='Exit',    command=self.root.quit)
        mb.add_cascade(label='File', menu=fm)
        self.root.config(menu=mb)
        self.root.bind('<Control-o>', lambda e: self.cmd_open())
        self.root.bind('<Control-s>', lambda e: self.cmd_save())

    def _build_toolbar(self):
        tb = tk.Frame(self.root, bg=BG_MID, pady=4)
        tb.pack(side=tk.TOP, fill=tk.X)

        def btn(text, cmd, **kw):
            b = tk.Button(tb, text=text, command=cmd, bg=BG_CARD, fg=FG,
                          relief=tk.FLAT, padx=10, pady=3, **kw)
            b.pack(side=tk.LEFT, padx=3)
            return b

        btn('Open',    self.cmd_open)
        btn('Save',    self.cmd_save)
        btn('Save As', self.cmd_save_as)

        self._change_lbl = tk.Label(tb, text='', fg=ACCENT, bg=BG_MID,
                                    font=('Segoe UI', 9, 'bold'))
        self._change_lbl.pack(side=tk.LEFT, padx=10)

    def _build_notebook(self):
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TNotebook',       background=BG_MID)
        style.configure('TNotebook.Tab',   background=BG_CARD,  foreground=FG,
                        padding=[10, 4])
        style.map('TNotebook.Tab',         background=[('selected', BG)])
        style.configure('Treeview',        background=BG_MID, fieldbackground=BG_MID,
                        foreground=FG, rowheight=22)
        style.configure('Treeview.Heading', background=BG_CARD, foreground=FG)
        style.map('Treeview',              background=[('selected', BG_CARD)])
        style.configure('TPanedwindow',    background=BG_MID)
        style.configure('TFrame',          background=BG_MID)

        nb = ttk.Notebook(self.root)
        nb.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))
        self._nb = nb

        self._build_browser_tab(nb)
        self._build_cyclist_tab(nb)

    def _build_statusbar(self):
        self._status = tk.StringVar(value='No file loaded.')
        bar = tk.Label(self.root, textvariable=self._status, anchor='w',
                       bg=BG_CARD, fg=FG_DIM, padx=6, pady=2)
        bar.pack(side=tk.BOTTOM, fill=tk.X)

    # ── Database Browser tab ──────────────────────────────────────────────────

    def _build_browser_tab(self, nb):
        outer = ttk.Frame(nb)
        nb.add(outer, text='  Database Browser  ')

        pw = ttk.PanedWindow(outer, orient=tk.HORIZONTAL)
        pw.pack(fill=tk.BOTH, expand=True)

        # Left: table list
        lf = ttk.Frame(pw, width=220)
        pw.add(lf, weight=1)

        tk.Label(lf, text='Tables', bg=BG_MID, fg=FG,
                 font=('Segoe UI', 10, 'bold')).pack(anchor='w', padx=6, pady=(6, 2))

        self._tbl_tree = ttk.Treeview(lf, show='tree', selectmode='browse')
        sb = ttk.Scrollbar(lf, orient=tk.VERTICAL, command=self._tbl_tree.yview)
        self._tbl_tree.configure(yscrollcommand=sb.set)
        self._tbl_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._tbl_tree.bind('<<TreeviewSelect>>', self._on_table_select)

        # Right: data grid
        rf = ttk.Frame(pw)
        pw.add(rf, weight=5)

        self._grid_title = tk.Label(rf, text='Select a table →', bg=BG_MID, fg=FG_DIM,
                                    font=('Segoe UI', 10))
        self._grid_title.pack(anchor='w', padx=6, pady=(6, 2))

        gf = ttk.Frame(rf)
        gf.pack(fill=tk.BOTH, expand=True)

        self._data_grid = ttk.Treeview(gf)
        hscr = ttk.Scrollbar(gf, orient=tk.HORIZONTAL, command=self._data_grid.xview)
        vscr = ttk.Scrollbar(gf, orient=tk.VERTICAL,   command=self._data_grid.yview)
        self._data_grid.configure(xscrollcommand=hscr.set, yscrollcommand=vscr.set)
        hscr.pack(side=tk.BOTTOM, fill=tk.X)
        vscr.pack(side=tk.RIGHT,  fill=tk.Y)
        self._data_grid.pack(fill=tk.BOTH, expand=True)

    # ── Cyclist Editor tab ────────────────────────────────────────────────────

    def _build_cyclist_tab(self, nb):
        outer = ttk.Frame(nb)
        nb.add(outer, text='  Cyclist Editor  ')

        # ── Search bar ────────────────────────────────────────────────────────
        sf = tk.Frame(outer, bg=BG_MID)
        sf.pack(fill=tk.X, padx=8, pady=6)
        tk.Label(sf, text='Search:', bg=BG_MID, fg=FG).pack(side=tk.LEFT)
        self._q = tk.StringVar()
        ent = tk.Entry(sf, textvariable=self._q, width=32,
                       bg=BG_CARD, fg=FG, insertbackground=FG,
                       relief=tk.FLAT, font=('Segoe UI', 10))
        ent.pack(side=tk.LEFT, padx=6, ipady=3)
        ent.bind('<Return>', lambda e: self.cmd_search())
        tk.Button(sf, text='Search', command=self.cmd_search,
                  bg=ACCENT, fg='white', relief=tk.FLAT, padx=10).pack(side=tk.LEFT)
        self._search_info = tk.Label(sf, text='', bg=BG_MID, fg=FG_DIM,
                                     font=('Segoe UI', 9))
        self._search_info.pack(side=tk.LEFT, padx=10)

        # ── Main pane ─────────────────────────────────────────────────────────
        pw = ttk.PanedWindow(outer, orient=tk.HORIZONTAL)
        pw.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

        # Left: results list
        lf = ttk.Frame(pw, width=260)
        pw.add(lf, weight=1)

        self._res_list = ttk.Treeview(lf, columns=('name', 'team'),
                                      show='headings', selectmode='browse')
        self._res_list.heading('name', text='Name')
        self._res_list.heading('team', text='Team ID')
        self._res_list.column('name', width=170)
        self._res_list.column('team', width=60, anchor='center')
        rs = ttk.Scrollbar(lf, orient=tk.VERTICAL, command=self._res_list.yview)
        self._res_list.configure(yscrollcommand=rs.set)
        self._res_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        rs.pack(side=tk.RIGHT, fill=tk.Y)
        self._res_list.bind('<<TreeviewSelect>>', self._on_cyclist_select)

        # Right: detail panel
        rf = ttk.Frame(pw)
        pw.add(rf, weight=3)
        self._build_detail_panel(rf)

    def _build_detail_panel(self, parent):
        # Name + info header
        hf = tk.Frame(parent, bg=BG_MID)
        hf.pack(fill=tk.X, padx=8, pady=(8, 0))

        self._cy_name  = tk.Label(hf, text='', bg=BG_MID, fg=FG,
                                   font=('Segoe UI', 16, 'bold'))
        self._cy_name.pack(anchor='w')
        self._cy_info  = tk.Label(hf, text='', bg=BG_MID, fg=FG_DIM,
                                   font=('Segoe UI', 9))
        self._cy_info.pack(anchor='w')

        sep = tk.Frame(parent, height=1, bg=BG_CARD)
        sep.pack(fill=tk.X, padx=8, pady=4)

        # Two-column layout: stats on left, extra info on right
        body = tk.Frame(parent, bg=BG_MID)
        body.pack(fill=tk.BOTH, expand=True, padx=8)

        # Stats canvas (left)
        lf = ttk.LabelFrame(body, text=' Attributes ', padding=4)
        lf.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        canvas_frame = tk.Frame(lf, bg=BG)
        canvas_frame.pack(fill=tk.BOTH, expand=True)

        self._stat_canvas = tk.Canvas(canvas_frame, bg=BG, highlightthickness=0,
                                      cursor='hand2')
        sv = ttk.Scrollbar(canvas_frame, orient=tk.VERTICAL,
                           command=self._stat_canvas.yview)
        self._stat_canvas.configure(yscrollcommand=sv.set)
        sv.pack(side=tk.RIGHT, fill=tk.Y)
        self._stat_canvas.pack(fill=tk.BOTH, expand=True)
        self._stat_canvas.bind('<Configure>', self._on_canvas_resize)
        self._stat_canvas.bind('<Button-1>', self._on_canvas_click)

        # Extra info panel (right)
        rf = tk.Frame(body, bg=BG_MID, width=200)
        rf.pack(side=tk.RIGHT, fill=tk.Y, padx=(8, 0))
        rf.pack_propagate(False)

        tk.Label(rf, text='Additional Info', bg=BG_MID, fg=FG,
                 font=('Segoe UI', 10, 'bold')).pack(anchor='w', pady=(4, 8))

        self._extra_vars = {}
        for key, label in [
            ('popularity',     'Popularity'),
            ('ability',        'Current Ability'),
            ('potential',      'Potential'),
            ('birthdate',      'Born'),
            ('team_id',        'Team ID'),
            ('rider_type',     'Rider Type'),
            ('state',          'State'),
        ]:
            row = tk.Frame(rf, bg=BG_MID)
            row.pack(fill=tk.X, pady=1)
            tk.Label(row, text=f'{label}:', bg=BG_MID, fg=FG_DIM,
                     font=('Segoe UI', 9), width=14, anchor='w').pack(side=tk.LEFT)
            var = tk.StringVar()
            tk.Label(row, textvariable=var, bg=BG_MID, fg=FG,
                     font=('Segoe UI', 9, 'bold')).pack(side=tk.LEFT)
            self._extra_vars[key] = var

    # ── Commands ──────────────────────────────────────────────────────────────

    def cmd_open(self):
        initial = os.path.dirname(os.path.abspath(__file__))
        path = filedialog.askopenfilename(
            title='Open PCM Database (.cdb)',
            filetypes=[('Cyanide Database', '*.cdb'), ('All Files', '*.*')],
            initialdir=initial,
        )
        if path:
            self._load(path)

    def cmd_save(self):
        if not self.db:
            messagebox.showwarning('No file', 'No database is loaded.')
            return
        if not self._changes:
            messagebox.showinfo('No changes', 'Nothing to save.')
            return
        n = len(self._changes)
        if messagebox.askyesno('Confirm Save',
                               f'Save {n} change(s) to:\n{self.db.filepath}\n\n'
                               f'A backup (.bak) will be created automatically.'):
            try:
                self.db.save()
                self._changes.clear()
                self._change_lbl.config(text='')
                self._set_status(f'Saved → {self.db.filepath}')
            except Exception as exc:
                messagebox.showerror('Save Error', str(exc))

    def cmd_save_as(self):
        if not self.db:
            return
        path = filedialog.asksaveasfilename(
            title='Save As',
            filetypes=[('Cyanide Database', '*.cdb'), ('All Files', '*.*')],
            defaultextension='.cdb',
        )
        if path:
            try:
                self.db.save(path)
                self._set_status(f'Saved → {path}')
            except Exception as exc:
                messagebox.showerror('Save Error', str(exc))

    def cmd_search(self):
        if not self.db:
            messagebox.showinfo('No file', 'Open a .cdb file first.')
            return
        if 'DYN_cyclist' not in self.db.tables:
            messagebox.showinfo('Not found', 'DYN_cyclist table not found.')
            return
        self._do_search(self._q.get().strip().lower())

    # ── Loading ───────────────────────────────────────────────────────────────

    def _load(self, path):
        self._set_status(f'Loading {path} …')
        self.root.update_idletasks()
        try:
            self.db = CDBFile(path)
        except Exception as exc:
            messagebox.showerror('Load Error', str(exc))
            self._set_status('Load failed.')
            return

        self._changes.clear()
        self._change_lbl.config(text='')
        self._populate_table_list()
        n_cy = self.db.get_record_count('DYN_cyclist')
        self._set_status(
            f'{os.path.basename(path)}  ·  {len(self.db.tables)} tables  ·  {n_cy} cyclists'
        )

    # ── Browser ───────────────────────────────────────────────────────────────

    def _populate_table_list(self):
        self._tbl_tree.delete(*self._tbl_tree.get_children())
        for name in sorted(self.db.tables.keys()):
            n = self.db.tables[name]['record_count']
            self._tbl_tree.insert('', 'end', iid=name, text=f'  {name}  ({n:,})')

    def _on_table_select(self, _event):
        sel = self._tbl_tree.selection()
        if not sel or not self.db:
            return
        self._populate_grid(sel[0])

    def _populate_grid(self, table_name):
        tbl = self.db.tables.get(table_name)
        if not tbl:
            return

        n_rec   = tbl['record_count']
        all_fld = list(tbl['fields'].keys())
        cols    = all_fld[:24]          # cap at 24 columns for readability
        limit   = min(n_rec, 500)

        self._grid_title.config(
            text=f'{table_name}   ·   {n_rec:,} records   ·   '
                 f'{len(all_fld)} fields'
                 + (f'   (showing first {limit})' if limit < n_rec else '')
        )

        self._data_grid.delete(*self._data_grid.get_children())
        self._data_grid['columns'] = cols
        self._data_grid['show']    = 'headings'

        for c in cols:
            self._data_grid.heading(c, text=c)
            self._data_grid.column(c, width=130, minwidth=80, stretch=False)

        # Pre-load columns
        col_data = {c: self.db.get_column(table_name, c) for c in cols}

        for i in range(limit):
            row = [_fmt_val(col_data[c][i] if i < len(col_data[c]) else None)
                   for c in cols]
            self._data_grid.insert('', 'end', values=row)

    # ── Cyclist search ────────────────────────────────────────────────────────

    def _do_search(self, query):
        self._res_list.delete(*self._res_list.get_children())
        self._search_results = []

        tbl    = self.db.tables['DYN_cyclist']
        fields = tbl['fields']
        n      = tbl['record_count']

        def _get_entries(fname):
            f = fields.get(fname, {})
            return f.get('pool_entries') or []

        ln_entries = _get_entries('gene_sz_lastname')
        fn_entries = _get_entries('gene_sz_firstname')
        team_vals  = (fields.get('fkIDteam') or {}).get('values', b'')

        results = []
        for i in range(n):
            ln = ln_entries[i] if i < len(ln_entries) else ''
            fn = fn_entries[i] if i < len(fn_entries) else ''
            full = f'{fn} {ln}'.strip()
            if not query or query in full.lower():
                team = ''
                if team_vals and i * 4 + 4 <= len(team_vals):
                    team = str(_u32(team_vals, i * 4))
                results.append((i, full, team))

        # Sort alphabetically by name
        results.sort(key=lambda x: x[1].lower())
        display = results[:300]

        for idx, name, team in display:
            self._res_list.insert('', 'end', iid=str(idx), values=(name, team))

        self._search_results = display
        cnt = len(results)
        extra = f' (showing {len(display)})' if cnt > len(display) else ''
        self._search_info.config(text=f'{cnt} found{extra}')

    def _on_cyclist_select(self, _event):
        sel = self._res_list.selection()
        if sel:
            self._show_cyclist(int(sel[0]))

    # ── Cyclist detail ────────────────────────────────────────────────────────

    def _show_cyclist(self, idx):
        if not self.db or 'DYN_cyclist' not in self.db.tables:
            return
        self._cyclist_idx = idx

        fields = self.db.tables['DYN_cyclist']['fields']

        def strval(fname):
            f = fields.get(fname, {})
            entries = f.get('pool_entries') or []
            return entries[idx] if idx < len(entries) else ''

        def intval(fname):
            f = fields.get(fname, {})
            v = f.get('values', b'')
            if v and idx * 4 + 4 <= len(v):
                return _u32(v, idx * 4)
            return 0

        def fltval(fname):
            f = fields.get(fname, {})
            v = f.get('values', b'')
            if v and idx * 4 + 4 <= len(v):
                return _f32(v, idx * 4)
            return 0.0

        fn   = strval('gene_sz_firstname')
        ln   = strval('gene_sz_lastname')
        name = f'{fn} {ln}'.strip() or f'Cyclist #{idx}'

        self._cy_name.config(text=name)
        bdate = intval('gene_i_birthdate')
        self._cy_info.config(text=f'Index: {idx}   ·   ID: {intval("IDcyclist")}')

        # Extra info panel
        pop = fltval('gene_f_popularity')
        self._extra_vars['popularity'].set(f'{pop:.2f}')
        self._extra_vars['ability'].set(f'{fltval("value_f_current_ability"):.2f}')
        self._extra_vars['potential'].set(f'{fltval("value_f_potentiel"):.2f}')
        self._extra_vars['birthdate'].set(_bdate_str(bdate))
        self._extra_vars['team_id'].set(str(intval('fkIDteam')))
        rtype = intval('fkIDtype_rider')
        self._extra_vars['rider_type'].set(RIDER_TYPES.get(rtype, str(rtype)))
        self._extra_vars['state'].set(str(intval('fkIDcyclist_state')))

        self._draw_stats(idx)

    def _draw_stats(self, idx):
        c = self._stat_canvas
        c.delete('all')
        W = c.winfo_width() or 600
        if W < 200:
            W = 600

        ROW_H    = 42
        LABEL_W  = 130
        BAR_PAD  = 8
        BAR_H    = 20
        NUM_W    = 90
        TOTAL_H  = ROW_H * len(CYCLIST_STATS) + 20
        c.configure(scrollregion=(0, 0, W, TOTAL_H))

        BAR_W = max(W - LABEL_W - BAR_PAD * 2 - NUM_W - 10, 80)

        for i, (label, cf, lf) in enumerate(CYCLIST_STATS):
            y0 = i * ROW_H + 10
            yc = y0 + ROW_H // 2 - BAR_H // 2

            cur = self.db.get_stat_byte('DYN_cyclist', cf, idx)
            pot = self.db.get_stat_byte('DYN_cyclist', lf, idx)

            # Background
            x0b = LABEL_W + BAR_PAD
            c.create_rectangle(x0b, yc, x0b + BAR_W, yc + BAR_H,
                                fill=BAR_BG, outline='#404070')

            # Potential bar
            if pot > 0:
                pw = int(BAR_W * pot / 100)
                c.create_rectangle(x0b, yc, x0b + pw, yc + BAR_H,
                                   fill=BAR_POT, outline='')

            # Current bar
            if cur > 0:
                cw = int(BAR_W * cur / 100)
                c.create_rectangle(x0b, yc, x0b + cw, yc + BAR_H,
                                   fill=_bar_color(cur), outline='')

            # Label
            c.create_text(LABEL_W - 6, yc + BAR_H // 2, text=label,
                          anchor='e', fill=FG, font=('Segoe UI', 9))

            # Value text
            c.create_text(x0b + BAR_W + 6, yc + BAR_H // 2,
                          text=f'{cur} / {pot}',
                          anchor='w', fill=FG_DIM, font=('Segoe UI', 8))

    def _on_canvas_resize(self, _event):
        if self._cyclist_idx is not None:
            self._draw_stats(self._cyclist_idx)

    def _on_canvas_click(self, event):
        if self._cyclist_idx is None or not self.db:
            return
        ROW_H  = 42
        Y0     = 10
        row    = (event.y - Y0) // ROW_H
        if 0 <= row < len(CYCLIST_STATS):
            label, cf, lf = CYCLIST_STATS[row]
            cur = self.db.get_stat_byte('DYN_cyclist', cf, self._cyclist_idx)
            pot = self.db.get_stat_byte('DYN_cyclist', lf, self._cyclist_idx)
            self._edit_stat_dialog(label, cf, lf, cur, pot)

    def _edit_stat_dialog(self, label, cf, lf, cur, pot):
        idx = self._cyclist_idx
        dlg = tk.Toplevel(self.root)
        dlg.title(f'Edit  —  {label}')
        dlg.geometry('320x200')
        dlg.resizable(False, False)
        dlg.configure(bg=BG_MID)
        dlg.transient(self.root)
        dlg.grab_set()

        tk.Label(dlg, text=f'Editing: {label}', bg=BG_MID, fg=FG,
                 font=('Segoe UI', 12, 'bold')).pack(pady=(14, 8))

        grid = tk.Frame(dlg, bg=BG_MID)
        grid.pack()

        def lbl(text, r, c):
            tk.Label(grid, text=text, bg=BG_MID, fg=FG_DIM,
                     font=('Segoe UI', 9)).grid(row=r, column=c, sticky='e',
                                                padx=8, pady=4)

        lbl('Current (0–100):', 0, 0)
        cur_var = tk.IntVar(value=cur)
        cur_sb  = tk.Spinbox(grid, from_=0, to=100, textvariable=cur_var,
                             width=7, bg=BG_CARD, fg=FG,
                             buttonbackground=BG_CARD, relief=tk.FLAT)
        cur_sb.grid(row=0, column=1, padx=8, pady=4)

        lbl('Potential (0–100):', 1, 0)
        pot_var = tk.IntVar(value=pot)
        pot_sb  = tk.Spinbox(grid, from_=0, to=100, textvariable=pot_var,
                             width=7, bg=BG_CARD, fg=FG,
                             buttonbackground=BG_CARD, relief=tk.FLAT)
        pot_sb.grid(row=1, column=1, padx=8, pady=4)

        def apply_():
            new_cur = max(0, min(100, cur_var.get()))
            new_pot = max(0, min(100, pot_var.get()))
            self.db.set_stat_byte('DYN_cyclist', cf,  idx, new_cur)
            self.db.set_stat_byte('DYN_cyclist', lf,  idx, new_pot)
            self._changes[('DYN_cyclist', cf,  idx)] = new_cur
            self._changes[('DYN_cyclist', lf,  idx)] = new_pot
            self._change_lbl.config(text=f'⚠  {len(self._changes)} unsaved change(s)')
            self._draw_stats(idx)
            dlg.destroy()

        bf = tk.Frame(dlg, bg=BG_MID)
        bf.pack(pady=12)
        tk.Button(bf, text='Apply', command=apply_,
                  bg=ACCENT, fg='white', relief=tk.FLAT, padx=14, pady=4).pack(side=tk.LEFT, padx=6)
        tk.Button(bf, text='Cancel', command=dlg.destroy,
                  bg=BG_CARD, fg=FG, relief=tk.FLAT, padx=14, pady=4).pack(side=tk.LEFT, padx=6)

    # ── Utility ───────────────────────────────────────────────────────────────

    def _set_status(self, msg: str):
        self._status.set(msg)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    root = tk.Tk()
    app  = PCMEditorApp(root)

    # Auto-load the default file if present
    here    = os.path.dirname(os.path.abspath(__file__))
    default = os.path.join(here, 'Databasefile', 'JMac.cdb')
    if os.path.exists(default):
        app._load(default)

    root.mainloop()


if __name__ == '__main__':
    main()
