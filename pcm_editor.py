"""
pcm_editor.py – GUI for the PCM Database Editor.
Parses and writes .cdb files via cdb_file.CDBFile.
"""

import os, tkinter as tk
from tkinter import ttk, messagebox, filedialog

from cdb_file import CDBFile, _u32, _f32

# ─────────────────────────────────────────────────────────────────────────────
# GUI constants
# ─────────────────────────────────────────────────────────────────────────────

CYCLIST_STATS = [
    ('Flat',          'charac_i_plain',          'limit_i_plain'),
    ('Mountain',      'charac_i_mountain',        'limit_i_mountain'),
    ('Med. Mountain', 'charac_i_medium_mountain', 'limit_i_medium_mountain'),
    ('Downhill',      'charac_i_downhilling',     'limit_i_downhilling'),
    ('Cobbles',       'charac_i_cobble',           'limit_i_cobble'),
    ('Time Trial',    'charac_i_timetrial',        'limit_i_timetrial'),
    ('Prologue',      'charac_i_prologue',         'limit_i_prologue'),
    ('Sprint',        'charac_i_sprint',           'limit_i_sprint'),
    ('Acceleration',  'charac_i_acceleration',     'limit_i_acceleration'),
    ('Endurance',     'charac_i_endurance',        'limit_i_endurance'),
    ('Resistance',    'charac_i_resistance',       'limit_i_resistance'),
    ('Recovery',      'charac_i_recuperation',     'limit_i_recuperation'),
    ('Hill',          'charac_i_hill',             'limit_i_hill'),
    ('Baroudeur',     'charac_i_baroudeur',        'limit_i_baroudeur'),
]

RIDER_TYPES = {
    0: 'Unknown', 1: 'All-rounder', 2: 'Climber', 3: 'Sprinter',
    4: 'Time Trialist', 5: 'Classics', 6: 'Puncheur', 7: 'Sprinter',
}

BG      = '#1a1a2e'
BG_MID  = '#16213e'
BG_CARD = '#0f3460'
ACCENT  = '#e94560'
FG      = '#eaeaea'
FG_DIM  = '#888899'
BAR_BG  = '#2d2d4e'
BAR_POT = '#1a4a5a'


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _bar_color(value: int) -> str:
    """Return a hex colour for a stat bar scaled 0–100."""
    t = value / 100.0
    r = int(220 * (1 - t) + 60 * t)
    g = int(80  * (1 - t) + 200 * t)
    return f'#{r:02x}{g:02x}3c'


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
# Application
# ─────────────────────────────────────────────────────────────────────────────

class PCMEditorApp:

    def __init__(self, root: tk.Tk):
        self.root             = root
        self.db: CDBFile | None = None
        self._changes         = {}
        self._cyclist_idx: int | None = None
        self._search_results: list    = []

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
        fm.add_command(label='Open…',    command=self.cmd_open,    accelerator='Ctrl+O')
        fm.add_command(label='Save',     command=self.cmd_save,    accelerator='Ctrl+S')
        fm.add_command(label='Save As…', command=self.cmd_save_as)
        fm.add_separator()
        fm.add_command(label='Exit',     command=self.root.quit)
        mb.add_cascade(label='File', menu=fm)
        self.root.config(menu=mb)
        self.root.bind('<Control-o>', lambda e: self.cmd_open())
        self.root.bind('<Control-s>', lambda e: self.cmd_save())

    def _build_toolbar(self):
        tb = tk.Frame(self.root, bg=BG_MID, pady=4)
        tb.pack(side=tk.TOP, fill=tk.X)

        def btn(text, cmd):
            b = tk.Button(tb, text=text, command=cmd, bg=BG_CARD, fg=FG,
                          relief=tk.FLAT, padx=10, pady=3)
            b.pack(side=tk.LEFT, padx=3)

        btn('Open',    self.cmd_open)
        btn('Save',    self.cmd_save)
        btn('Save As', self.cmd_save_as)

        self._change_lbl = tk.Label(tb, text='', fg=ACCENT, bg=BG_MID,
                                    font=('Segoe UI', 9, 'bold'))
        self._change_lbl.pack(side=tk.LEFT, padx=10)

    def _build_notebook(self):
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TNotebook',        background=BG_MID)
        style.configure('TNotebook.Tab',    background=BG_CARD, foreground=FG, padding=[10, 4])
        style.map('TNotebook.Tab',          background=[('selected', BG)])
        style.configure('Treeview',         background=BG_MID, fieldbackground=BG_MID,
                        foreground=FG, rowheight=22)
        style.configure('Treeview.Heading', background=BG_CARD, foreground=FG)
        style.map('Treeview',               background=[('selected', BG_CARD)])
        style.configure('TPanedwindow',     background=BG_MID)
        style.configure('TFrame',           background=BG_MID)

        nb = ttk.Notebook(self.root)
        nb.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))
        self._nb = nb
        self._build_browser_tab(nb)
        self._build_cyclist_tab(nb)

    def _build_statusbar(self):
        self._status = tk.StringVar(value='No file loaded.')
        tk.Label(self.root, textvariable=self._status, anchor='w',
                 bg=BG_CARD, fg=FG_DIM, padx=6, pady=2).pack(side=tk.BOTTOM, fill=tk.X)

    # ── Database Browser tab ──────────────────────────────────────────────────

    def _build_browser_tab(self, nb):
        outer = ttk.Frame(nb)
        nb.add(outer, text='  Database Browser  ')

        pw = ttk.PanedWindow(outer, orient=tk.HORIZONTAL)
        pw.pack(fill=tk.BOTH, expand=True)

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

        pw = ttk.PanedWindow(outer, orient=tk.HORIZONTAL)
        pw.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

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

        rf = ttk.Frame(pw)
        pw.add(rf, weight=3)
        self._build_detail_panel(rf)

    def _build_detail_panel(self, parent):
        hf = tk.Frame(parent, bg=BG_MID)
        hf.pack(fill=tk.X, padx=8, pady=(8, 0))
        self._cy_name = tk.Label(hf, text='', bg=BG_MID, fg=FG,
                                  font=('Segoe UI', 16, 'bold'))
        self._cy_name.pack(anchor='w')
        self._cy_info = tk.Label(hf, text='', bg=BG_MID, fg=FG_DIM,
                                  font=('Segoe UI', 9))
        self._cy_info.pack(anchor='w')

        tk.Frame(parent, height=1, bg=BG_CARD).pack(fill=tk.X, padx=8, pady=4)

        body = tk.Frame(parent, bg=BG_MID)
        body.pack(fill=tk.BOTH, expand=True, padx=8)

        lf = ttk.LabelFrame(body, text=' Attributes ', padding=4)
        lf.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        cf = tk.Frame(lf, bg=BG)
        cf.pack(fill=tk.BOTH, expand=True)
        self._stat_canvas = tk.Canvas(cf, bg=BG, highlightthickness=0, cursor='hand2')
        sv = ttk.Scrollbar(cf, orient=tk.VERTICAL, command=self._stat_canvas.yview)
        self._stat_canvas.configure(yscrollcommand=sv.set)
        sv.pack(side=tk.RIGHT, fill=tk.Y)
        self._stat_canvas.pack(fill=tk.BOTH, expand=True)
        self._stat_canvas.bind('<Configure>', self._on_canvas_resize)
        self._stat_canvas.bind('<Button-1>',  self._on_canvas_click)

        rf = tk.Frame(body, bg=BG_MID, width=200)
        rf.pack(side=tk.RIGHT, fill=tk.Y, padx=(8, 0))
        rf.pack_propagate(False)
        tk.Label(rf, text='Additional Info', bg=BG_MID, fg=FG,
                 font=('Segoe UI', 10, 'bold')).pack(anchor='w', pady=(4, 8))

        self._extra_vars = {}
        for key, label in [
            ('popularity', 'Popularity'),
            ('ability',    'Current Ability'),
            ('potential',  'Potential'),
            ('birthdate',  'Born'),
            ('team_id',    'Team ID'),
            ('rider_type', 'Rider Type'),
            ('state',      'State'),
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
        path = filedialog.askopenfilename(
            title='Open PCM Database (.cdb)',
            filetypes=[('Cyanide Database', '*.cdb'), ('All Files', '*.*')],
            initialdir=os.path.dirname(os.path.abspath(__file__)),
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
                               'A backup (.bak) will be created automatically.'):
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
        if sel and self.db:
            self._populate_grid(sel[0])

    def _populate_grid(self, table_name):
        tbl = self.db.tables.get(table_name)
        if not tbl:
            return
        n_rec   = tbl['record_count']
        all_fld = list(tbl['fields'].keys())
        cols    = all_fld[:24]
        limit   = min(n_rec, 500)

        self._grid_title.config(
            text=f'{table_name}   ·   {n_rec:,} records   ·   {len(all_fld)} fields'
                 + (f'   (showing first {limit})' if limit < n_rec else '')
        )
        self._data_grid.delete(*self._data_grid.get_children())
        self._data_grid['columns'] = cols
        self._data_grid['show']    = 'headings'
        for c in cols:
            self._data_grid.heading(c, text=c)
            self._data_grid.column(c, width=130, minwidth=80, stretch=False)

        col_data = {c: self.db.get_column(table_name, c) for c in cols}
        for i in range(limit):
            row = [_fmt_val(col_data[c][i] if i < len(col_data[c]) else None) for c in cols]
            self._data_grid.insert('', 'end', values=row)

    # ── Cyclist search ────────────────────────────────────────────────────────

    def _do_search(self, query):
        self._res_list.delete(*self._res_list.get_children())
        self._search_results = []

        tbl    = self.db.tables['DYN_cyclist']
        fields = tbl['fields']
        n      = tbl['record_count']

        def entries(fname):
            return (fields.get(fname) or {}).get('pool_entries') or []

        ln_e      = entries('gene_sz_lastname')
        fn_e      = entries('gene_sz_firstname')
        team_vals = (fields.get('fkIDteam') or {}).get('values', b'')

        results = []
        for i in range(n):
            fn   = fn_e[i] if i < len(fn_e) else ''
            ln   = ln_e[i] if i < len(ln_e) else ''
            full = f'{fn} {ln}'.strip()
            if not query or query in full.lower():
                team = str(_u32(team_vals, i * 4)) if team_vals and i * 4 + 4 <= len(team_vals) else ''
                results.append((i, full, team))

        results.sort(key=lambda x: x[1].lower())
        display = results[:300]
        for idx, name, team in display:
            self._res_list.insert('', 'end', iid=str(idx), values=(name, team))
        self._search_results = display
        cnt   = len(results)
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
            e = (fields.get(fname) or {}).get('pool_entries') or []
            return e[idx] if idx < len(e) else ''

        def intval(fname):
            v = (fields.get(fname) or {}).get('values', b'')
            return _u32(v, idx * 4) if v and idx * 4 + 4 <= len(v) else 0

        def fltval(fname):
            v = (fields.get(fname) or {}).get('values', b'')
            return _f32(v, idx * 4) if v and idx * 4 + 4 <= len(v) else 0.0

        name = f'{strval("gene_sz_firstname")} {strval("gene_sz_lastname")}'.strip() or f'Cyclist #{idx}'
        self._cy_name.config(text=name)
        self._cy_info.config(text=f'Index: {idx}   ·   ID: {intval("IDcyclist")}')

        self._extra_vars['popularity'].set(f'{fltval("gene_f_popularity"):.2f}')
        self._extra_vars['ability'].set(f'{fltval("value_f_current_ability"):.2f}')
        self._extra_vars['potential'].set(f'{fltval("value_f_potentiel"):.2f}')
        self._extra_vars['birthdate'].set(_bdate_str(intval('gene_i_birthdate')))
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

        ROW_H   = 42
        LABEL_W = 130
        BAR_PAD = 8
        BAR_H   = 20
        NUM_W   = 60
        TOTAL_H = ROW_H * len(CYCLIST_STATS) + 20
        c.configure(scrollregion=(0, 0, W, TOTAL_H))
        BAR_W = max(W - LABEL_W - BAR_PAD * 2 - NUM_W - 10, 80)

        for i, (label, cf, lf) in enumerate(CYCLIST_STATS):
            yc = i * ROW_H + 10 + ROW_H // 2 - BAR_H // 2
            cur = self.db.get_stat_byte('DYN_cyclist', cf, idx)
            pot = self.db.get_stat_byte('DYN_cyclist', lf, idx)

            x0b = LABEL_W + BAR_PAD
            c.create_rectangle(x0b, yc, x0b + BAR_W, yc + BAR_H,
                                fill=BAR_BG, outline='#404070')
            if pot > 0:
                c.create_rectangle(x0b, yc, x0b + int(BAR_W * pot / 100), yc + BAR_H,
                                   fill=BAR_POT, outline='')
            if cur > 0:
                c.create_rectangle(x0b, yc, x0b + int(BAR_W * cur / 100), yc + BAR_H,
                                   fill=_bar_color(cur), outline='')

            c.create_text(LABEL_W - 6, yc + BAR_H // 2, text=label,
                          anchor='e', fill=FG, font=('Segoe UI', 9))
            c.create_text(x0b + BAR_W + 6, yc + BAR_H // 2,
                          text=f'{cur} / {pot}',
                          anchor='w', fill=FG_DIM, font=('Segoe UI', 8))

    def _on_canvas_resize(self, _event):
        if self._cyclist_idx is not None:
            self._draw_stats(self._cyclist_idx)

    def _on_canvas_click(self, event):
        if self._cyclist_idx is None or not self.db:
            return
        row = (event.y - 10) // 42
        if 0 <= row < len(CYCLIST_STATS):
            label, cf, lf = CYCLIST_STATS[row]
            cur = self.db.get_stat_byte('DYN_cyclist', cf, self._cyclist_idx)
            pot = self.db.get_stat_byte('DYN_cyclist', lf, self._cyclist_idx)
            self._edit_stat_dialog(label, cf, lf, cur, pot)

    def _edit_stat_dialog(self, label, cf, lf, cur, pot):
        idx = self._cyclist_idx
        dlg = tk.Toplevel(self.root)
        dlg.title(f'Edit  —  {label}')
        dlg.geometry('300x190')
        dlg.resizable(False, False)
        dlg.configure(bg=BG_MID)
        dlg.transient(self.root)
        dlg.grab_set()

        tk.Label(dlg, text=f'Editing: {label}', bg=BG_MID, fg=FG,
                 font=('Segoe UI', 12, 'bold')).pack(pady=(14, 8))

        grid = tk.Frame(dlg, bg=BG_MID)
        grid.pack()

        def lbl(text, r):
            tk.Label(grid, text=text, bg=BG_MID, fg=FG_DIM,
                     font=('Segoe UI', 9)).grid(row=r, column=0, sticky='e', padx=8, pady=4)

        lbl('Current (0–100):', 0)
        cur_var = tk.IntVar(value=cur)
        tk.Spinbox(grid, from_=0, to=100, textvariable=cur_var,
                   width=7, bg=BG_CARD, fg=FG,
                   buttonbackground=BG_CARD, relief=tk.FLAT).grid(row=0, column=1, padx=8, pady=4)

        lbl('Potential (0–100):', 1)
        pot_var = tk.IntVar(value=pot)
        tk.Spinbox(grid, from_=0, to=100, textvariable=pot_var,
                   width=7, bg=BG_CARD, fg=FG,
                   buttonbackground=BG_CARD, relief=tk.FLAT).grid(row=1, column=1, padx=8, pady=4)

        def apply_():
            new_cur = max(0, min(100, cur_var.get()))
            new_pot = max(0, min(100, pot_var.get()))
            self.db.set_stat_byte('DYN_cyclist', cf, idx, new_cur)
            self.db.set_stat_byte('DYN_cyclist', lf, idx, new_pot)
            self._changes[('DYN_cyclist', cf, idx)] = new_cur
            self._changes[('DYN_cyclist', lf, idx)] = new_pot
            self._change_lbl.config(text=f'⚠  {len(self._changes)} unsaved change(s)')
            self._draw_stats(idx)
            dlg.destroy()

        bf = tk.Frame(dlg, bg=BG_MID)
        bf.pack(pady=12)
        tk.Button(bf, text='Apply',  command=apply_,       bg=ACCENT,   fg='white',
                  relief=tk.FLAT, padx=14, pady=4).pack(side=tk.LEFT, padx=6)
        tk.Button(bf, text='Cancel', command=dlg.destroy,  bg=BG_CARD,  fg=FG,
                  relief=tk.FLAT, padx=14, pady=4).pack(side=tk.LEFT, padx=6)

    # ── Utility ───────────────────────────────────────────────────────────────

    def _set_status(self, msg: str):
        self._status.set(msg)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    root = tk.Tk()
    app  = PCMEditorApp(root)
    here    = os.path.dirname(os.path.abspath(__file__))
    default = os.path.join(here, 'Databasefile', 'JMac.cdb')
    if os.path.exists(default):
        app._load(default)
    root.mainloop()


if __name__ == '__main__':
    main()
