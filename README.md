## hltool

hltool is a simple utility to unpack and repack resource files from Android game Heroes Lore 5.

It currently supports unpacking and repacking resource files from the game's resource archive into a series of raw files.
It can also decode certain types of resource files into human-readable json files.
These json files can then be modified and repacked to form a new resource archive.

hltool is meant to be used with `data.vfs.mp3`. A file used by Heroes Lore 5 to store game assets, which is similar to zip file but without the compression.
This file contains everything the game needs to run (except for the code), which includes sprites, texts, etc.

### What it can do

The list is still quite short, support for more resource type will be added gradually. Although keep in mind that it is not guaranteed that it will be complete. Here's the list of what it can do.
- Unpack and repack `data.vfs.mp3`
- Encode and decode:
  - UI texts
  - Dialogues
  - Sprites
  - Item informations (name, description, price, for equipments: ATK and DEF)

hltool unpacks all files found in the game's resource archive to `raw` directory.
Therefore, the `raw` directory contains the unmodified, raw files as taken from the archive.
Some files in the `raw` directory are decoded, the decoded files will reside in separate directory.
The decoded file contains data represented in json or png (for sprites) that can be used to reconstruct the resource file in its original format.
Any changes made to its raw copy in the `raw` directory will be ignored.

The resulting json files sometimes turn out to be incomplete. This is because not all of the data inside the original file is identifiable by the tool.
These unknown data in the original file are simply written in json as integer with names such as "param" or "data" and should be left untouched.

## Usage

Before using hltool, you need to have `pillow` installed.
```
pip3 install pillow
```

If you're already familiar with `tar` then hltool should be quite easy.
Use `--help` for more information.
```
 * Extract a VFS archive data.vfs.mp3 to a directory called vfs
    python3 hltool.py -xf data.vfs.mp3 vfs
 * Create a VFS archive data.vfs.mp3 from a directory called vfs
    python3 hltool.py -cf data.vfs.mp3 vfs
```
