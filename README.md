## hltool

hltool is a simple utility to unpack and repack resource files from Android game Heroes Lore 5.

It currently supports unpacking and repacking resource files from the game's resource archive into a series of raw files (similar to unzipping).
And for some resource files, it also supports "decompiling", essentially converting it into a human-readable format as json.
hltool unpacks all files found in the game's resource archive to `raw` directory.
Therefore, the `raw` directory contains the unmodified, raw files as taken from the archive.
Certain files in the `raw` directory are "decompiled", creating a separate copy in a separate directory for each. The name of this directory depends on the file type.

The decompiled files contains data structure represented in json that will be used to reconstruct the files in original format upon repack operation.
Any changes made to its raw copy in the `raw` directory will be ignored.
The decompiled files can be modified by hand using text editor and repacked together with the other files to form a new archive.

Currently, hltool can decompile strings such as UI texts and dialogues.
It can also parse certain item's attributes (albeit still limited) such as price, and for equipments: ATK, and DEF.
Unknown fields in the raw files are simply written as integers with names such as '"param" or "data" and should be left untouched.

## Usage

If you're already familiar with `tar` then hltool should be quite easy.
Use `--help` for more information.
```
 * Extract a VFS archive data.vfs.mp3 to a directory called vfs
    python3 hltool.py -xf data.vfs.mp3 vfs
 * Create a VFS archive data.vfs.mp3 from a directory called vfs
    python3 hltool.py -cf data.vfs.mp3 vfs
```
