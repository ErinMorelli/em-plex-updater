# EM Plex Updater
A simple script for downloading and installing Plex Media Server updates for Linux.

## Basic Setup

Before starting, ensure you have the `python-yaml` and `python-lxml` packages installed on your system.

 1. Open the `sample_config.yml` file in your favorite text editor. 
     
 2. Read through each of the options and update/change them as needed.
     
 3. Save the file as `config.yml` and move it to:
    
        ~/.config/plex-updater/config.yml
     
     _**Note:** You will need to create the `plex-updater` (and potentially the `.config`) folder ahead of time. `mkdir -p ~/.config/plex-updater`_

 4. From within the `em-plex-updater` folder, run:
    
        ./plex_updater.py -c
    
    Check the output of this command for any errors or warnings related to your configuration.

You can also run `./plex_updater.py -h` at any time to view additional script options.


## Automatic Updates via Cron
A simple way to set up automatic updates is via cron. Below are some examples:

    0 3 * * 0,3 root /path/to/em-plex-updater/plex_updater.py

This will run the updater script automatically every Sunday and Wednesday at 3:00 AM.

If you want to log the output of the script to a file, try using this format:

    0 3 * * 0,3 root /path/to/em-plex-updater/plex_updater.py >> /path/to/logfile.log 2>&1

_**Note:** Make sure the user running the script running has permission to install packages via RPM/DPKG_
