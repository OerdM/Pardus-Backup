# Pardus Backup

Pardus Backup is a simple incremental backup application for debian based
operating systems.

It creates snapshots using rsync and hardlinks, so each backup can be browsed as
a complete copy while only the changed data occupies disk space.

It is currently a work in progress.

### **Dependencies**

This application is developed based on Python3 and GTK+ 3. Dependencies:

```
rsync python3-gi gir1.2-gtk-3.0
```

### **Run Application from Source**

Install dependencies

```
sudo apt install rsync python3-gi gir1.2-gtk-3.0
```

Clone the repository

```
git clone https://github.com/OerdM/Pardus-Backup.git ~/pardus-backup
```

Run application

```
python3 -m pardusbackup gui
```

### **Command Line Usage**

Create a backup. Multiple folders and single files can be combined; the first
run is a full copy, later runs are automatically incremental.

```
pardusbackup backup -s ~/Belgeler -s ~/Resimler -s ~/notlar.txt -d ~/yedekler
```

List, inspect and remove backups

```
pardusbackup list -d ~/yedekler
pardusbackup check -s ~/Belgeler -d ~/yedekler
pardusbackup delete ~/yedekler/2026-07-23_10-00-00
```

### **Build deb package**

```
sudo apt install devscripts equivs
sudo mk-build-deps -ir
dpkg-buildpackage -us -uc -b
```

The package is written to the parent directory. Install it with apt so that the
dependencies are resolved:

```
sudo apt install ../pardus-backup_0.1.0_all.deb
```

### **Tests**

```
sudo apt install python3-pytest
python3 -m pytest
```

### **Notes**

Backups must be stored on the same filesystem as the source, otherwise hardlinks
cannot be created and every backup becomes a full copy. This is verified before
a backup starts.

Backing up the whole system requires root privileges. Launching the application
from the menu runs it as the current user, which is sufficient for home
directory backups.

Restoring is not implemented yet. Snapshots are plain directories, so files can
be copied back with any file manager.

If the application is also installed with pipx, that version shadows the
packaged one and fails with `ModuleNotFoundError: No module named 'gi'`, because
an isolated virtual environment cannot see the system GTK bindings. Remove it
with `pipx uninstall pardusbackup`.

### **Screenshots**

### License

GNU General Public License v3.0 (GPL-3.0 license)
