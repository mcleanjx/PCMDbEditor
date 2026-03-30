# PCM Database Editor

A GUI tool for reading and editing Cyanide Studio `.cdb` save files from **Pro Cycling Manager**.

## Features

- **Database Browser** – view all 72 DYN_ tables and their records
- **Cyclist Editor** – search cyclists by name, view attributes as bar charts, edit stats
- Auto-backup (`.bak`) before saving changes

## Requirements

- Python 3.10+
- tkinter (included with standard Python on Windows)

## Usage

```
python pcm_editor.py
```

Place your `JMac.cdb` (and `.cdi`) file in a `Databasefile/` subfolder next to the script — it will load automatically on startup.

## File Format

The `.cdb` format is a proprietary Cyanide Studio database:
- 12-byte header: `FF FF FF FF` magic + uncompressed size + compressed size
- zlib-compressed payload containing DYN_ table blocks with sentinel markers
- Stat fields (`charac_i_*`, `limit_i_*`) are byte-packed: 4 values per uint32, range 0–100
- String fields store one null-terminated UTF-8 entry per record in a sequential pool
