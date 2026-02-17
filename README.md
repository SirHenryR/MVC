# MVC
Media Validation for Cellebrite JSON

## Purpose of script

The script renames the files that have been exported using PA to their original filename, if a file with the name exists, an increasing number is added to the file name.
In addition the files are checked whether they are a valid media file (image/video). If not, the file is deleted (or moved with witch -m).

The script is meant to reduce the exported files to those that are working and rename them to their original filenames (probably more useful for data recovery than forensics).

## Command line switches

-h    print help  
-p    check for missing Python packages  
-m    don't delete files, just move valid files to ./valid/, invalid files to ./invalid/  
-c    Cleanup mode: deletes all invalid media files recursively (watch for correct path yourself!)  
-l    optional logging into <case>.log  
no switch: rename files and delete invalid media files

Logfile will be appended if existing. 

There is a timeout set to 15 seconds (see sourcecode) for the checking.
