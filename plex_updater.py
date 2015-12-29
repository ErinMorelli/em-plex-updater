#!/usr/bin/env python

# Plex Updater
# Copyright (c) 2015 Erin Morelli
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
''' A python-based updater for Plex Media Server
'''

import re
import os
import time
import yaml
import urllib
import argparse
import requests
import subprocess
from lxml import html
import xml.etree.ElementTree as ET
import mediahandler.util.config as mhconfig
import mediahandler.util.notify as mhnotify


# Script global constants
API_ROOT_URL = 'https://plex.tv/{0}'
DPKG_EXECUTABLE = '/usr/bin/dpkg'
CONFIG_FILE = os.path.join(
    os.path.expanduser('~'), '.config', 'plex', 'config.yml')


def get_args():
    ''' Parse CLI arguments
    '''

    # Set up arg parser
    parser = argparse.ArgumentParser(
        description=(
            "Checks to see if there's a new Plex Media Server version," +
            "then downloads and installs it."
        )
    )

    # Add args to parser
    parser.add_argument(
        '-s', '--skip_install',
        default=False,
        help="Only downloads the new version file, does not install it.",
        action='store_true'
    )
    parser.add_argument(
        '-c', '--check_only',
        default=False,
        help="Only checks for newer version, does not download or install it.",
        action='store_true'
    )

    # Return parsed args
    return parser.parse_args()


def get_config():
    ''' Get extra info from config file
    '''

    # Read yaml config file
    config = open(CONFIG_FILE).read()

    # Return decoded config info
    return yaml.load(config)


def get_token(config):
    ''' Sign in and get token from Plex
    '''

    # Set up API sign in URI
    sign_in_url = API_ROOT_URL.format('users/sign_in.xml')

    # Make API post request to get token
    sign_in_resp = requests.post(
        sign_in_url,
        data={},
        headers={
            'X-Plex-Client-Identifier': config['client']
        },
        auth=(config['username'], config['password'])
    )

    # Return token from XML response
    return ET.fromstring(sign_in_resp.content).get('authenticationToken')


def get_server_info(token):
    ''' Get current server version
    '''

    # Set up API server info URI
    server_url = API_ROOT_URL.format('pms/servers.xml')

    # Make API get request for server info
    server_resp = requests.get(
        server_url,
        headers={
            'X-Plex-Token': token
        }
    )

    # Decode returned XML data
    server_xml = ET.fromstring(server_resp.content)[0]

    # Return server Plex version info
    return {
        'updated': int(server_xml.get('updatedAt')),
        'version': server_xml.get('version')
    }


def get_download_info(token):
    ''' Get current Plex version
    '''

    # Set up API downloads URI
    downloads_url = API_ROOT_URL.format('downloads')

    # Make API get request to downloads page
    downloads_resp = requests.get(
        downloads_url,
        params={
            'channel': 'plexpass'
        },
        headers={
            'X-Plex-Token': token
        }
    )

    # Decode returned HTML page content
    downloads_xml = html.fromstring(
        downloads_resp.content).xpath('//ul[@class="os"]/li')

    # Iterate over HTML content to extract needed info
    for download in downloads_xml:
        # We only care about the Ubuntu Linux version
        if download.find('span[@class="linux ubuntu"]') is not None:

            # Extract info from this section
            download_info = download.find('p[@class="sm"]').text_content()
            download_info_search = re.search(
                r'version (.*)\n\s+(.*)\n', download_info, re.I | re.M)

            # Set up version info from RegEx
            download_version = download_info_search.group(1)

            # Set up last updated info from Regex and convert to UNIX time
            download_updated_raw = download_info_search.group(2)
            download_updated = time.mktime(
                time.strptime(download_updated_raw, "%b %d, %Y"))

            # Extract file download link from content
            download_link = download.find(
                'div/a[@data-event-label="Ubuntu64"]').get('href')

            # Return available Plex download info
            return {
                'updated': download_updated,
                'version': download_version,
                'link': download_link
            }


def has_newer_version(server, download):
    ''' Check if Plex's version is newer than server version
    '''

    # Split version numbers into pieces for comparison
    s_version = server['version'].split('.')
    d_version = download['version'].split('.')

    def compare_versions(server, download):
        ''' Iteratively compare server and download version ints
        '''

        try:
            s_int = server.pop(0)
            d_int = download.pop(0)
        except IndexError:
            # If we've reached the end of our arrays, return
            return False

        # If the numbers are the same, keep going
        if d_int == s_int:
            return compare_versions(server, download)
        # If the download's number is higher, it's a newer version
        if d_int > s_int:
            return True
        # If the server's number is higher, it's an older version
        if d_int < s_int:
            return False

    # Recursively compare versions
    return compare_versions(s_version, d_version)


def install_update(download, config, args):
    ''' Download and install new version of Plex
    '''

    # Set up download name and path
    download_name = 'pms_{0}.deb'.format(download['version'])
    download_path = os.path.join(config['folder'], download_name)

    # If we've already downloaded this file, remove it
    if os.path.exists(download_path):
        os.remove(download_path)

    # Download the file
    downloader = urllib.URLopener()
    downloader.retrieve(download['link'], download_path)

    # Make sure the file exists
    if os.path.exists(download_path):
        # Install it with dpkg
        if not args.skip_install:
            subprocess.check_output([DPKG_EXECUTABLE, '-i', download_path])
        return True

    return False


def send_notification(message, error=False):
    ''' Send pushover notification
    '''

    # Setup push notification using EM Media Handler notification settings
    config_path = mhconfig.make_config(None)
    settings = mhconfig.parse_config(config_path)
    push = mhnotify.MHPush(settings['Notifications'])

    # Set success message
    msg_title = 'Plex Updated'

    # Set error message
    if error:
        msg_title = 'Error Updating Plex'

    # Send message
    push.send_message(message, msg_title)


def main():
    ''' Main wrapper function to run script
    '''

    # Start program
    print time.ctime()

    # Get CLI args
    args = get_args()

    # Get config file info
    config = get_config()

    # Get token
    token = get_token(config)

    # Get server info
    server = get_server_info(token)
    print 'Server Version: {0}'.format(server['version'])

    # Get download info
    download = get_download_info(token)

    # Check for new version
    if has_newer_version(server, download):
        print 'New version available: {0}'.format(download['version'])

        if not args.check_only:
            success = install_update(download, config, args)

            if success and not args.skip_install:
                msg = 'Plex has been updated to version {0}'.format(
                    download['version'])
            elif success and args.skip_install:
                msg = 'New Plex version {0} has been downloaded'.format(
                    download['version'])
            else:
                msg = 'There was an problem downloading the new Plex version.'

            send_notification(msg, not success)
            print msg
    else:
        print 'Plex is up-to-date!'


if __name__ == '__main__':
    # Run this thing
    main()
