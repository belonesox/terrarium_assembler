#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import shutil
import pathlib
import stat
import re
import sys
import fcntl
import struct

def main():
    hd_driveid_format_str = "@ 10H 20s 3H 8s 40s 2B H 2B H 4B 6H 2B I 36H I Q 152H"
    sizeof_hd_driveid = struct.calcsize(hd_driveid_format_str)
    HDIO_GET_IDENTITY = 0x030d
    assert sizeof_hd_driveid == 512 

    #Потом надо сделать хитрое вычисление рутового диска, но в 99.99…% все стоит на /dev/sda
    root_disk = "/dev/sda"
    # Call native function
    fd = open(root_disk, "rb")
    buf = fcntl.ioctl(fd, HDIO_GET_IDENTITY, " " * sizeof_hd_driveid)
    fields = struct.unpack(hd_driveid_format_str, buf)
    root_disk_serial = fields[10].strip().decode('utf-8')


    print("""
    Сейчас будет произведена инсталляция системы на диск с серийным номером
    """  + root_disk_serial)


    exepath = "%(instdir)s/ebin/technodemo" % vars()

    desktopfile = """
    [Desktop Entry]
    Name=Technodemo
    Comment=Run Technodemo on current XSession.
    Exec=%(exepath)s
    Terminal=false
    Type=Application
    #Encoding=UTF-8
    Icon=xterm-color
    Categories=System;TerminalEmulator;Utility;
    """ % vars()

    desktopfile = "/usr/share/applications/technodemo.desktop"
    with open(desktopfile, "w", encoding="utf-8") as lf:
        lf.write(desktopfile)

    st = os.stat(desktopfile)
    os.chmod(desktopfile, st.st_mode | stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)   


    print("""
    Инсталляция завершена.

    ------------------------------------------------------
    Удачи, и хорошего дня!
    """ % vars())


if __name__ == "__main__":
    main()