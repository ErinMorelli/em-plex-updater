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

from __future__ import print_function

import re
import os
import sys
import time
import yaml
import urllib
import argparse
import requests
import subprocess
from lxml import html
import xml.etree.ElementTree as ET


# Script global constants
API_ROOT_URL = 'https://plex.tv/{0}'
DPKG_EXECUTABLE = '/usr/bin/dpkg'
RPM_EXECUTABLE = '/bin/rpm'
VALID_SYSTEMS = ['Ubuntu', 'Fedora', 'CentOS']
CONFIG_FILE = os.path.join(
    os.path.expanduser('~'), '.config', 'plex-updater', 'config.yml')


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
    config_raw = open(CONFIG_FILE).read()

    # Decode yaml
    config = yaml.load(config_raw)

    # Check for valid systme value
    if config['linux_system'] not in VALID_SYSTEMS:
        msg = "Value for 'linux_system' must be one of: {0}"
        sys.exit(msg.format(', '.join(VALID_SYSTEMS)))

    # Check for valid version value
    if config['linux_version'] not in [32, 64]:
        sys.exit("Value for 'linux_version' must be one of: 32, 64")

    # Check for valid download folder
    if not os.path.exists(config['folder']):
        msg = "Path to 'folder' does not exist: {0}"
        sys.exit(msg.format(config['folder']))

    # Return config info
    return config


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

    # Get XML reponse
    sign_in_xml = ET.fromstring(sign_in_resp.content)

    def disable_plex_pass(config):
        ''' Disables use of the Plex Pass download feed
            and notifies the user.
        '''

        # Update config object to disable Plex Pass features
        config['plex_pass'] = False

        # Notify the user via CLI of the change
        print('WARNING: The account provided does not have a ' +
              'valid Plex Pass subscription!\n\t Switching to use the ' +
              'Public downloads feed for updates.', file=sys.stderr)

    # Check that user has Plex Pass access if they've enabled it
    if config['plex_pass']:
        subscription = sign_in_xml.find('subscription')

        # If the user has no subscription at all, disable
        if subscription is None:
            disable_plex_pass(config)

        # If the user's subscription is not active, disable
        elif subscription.get('active') == '0':
            disable_plex_pass(config)

        # If the user's subscription does not include 'pass', disable
        elif subscription.find('feature[@id="pass"]') is None:
            disable_plex_pass(config)

    # Return token from XML response
    return sign_in_xml.get('authenticationToken')


def get_server_info(token, args):
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

    # Make sure user is server owner
    if server_xml.get('owned') == '0':
        print('WARNING: The account provided does not have ownership ' +
              'permissions for this server!\n\t Updates can only ' +
              'be downloaded, not installed.', file=sys.stderr)

        # Enable 'skip_install'
        args.skip_install = True

    # Return server Plex version info
    return {
        'updated': int(server_xml.get('updatedAt')),
        'version': server_xml.get('version')
    }


def get_download_info(token, config):
    ''' Get current Plex version
    '''

    # Set up API downloads URI
    downloads_url = API_ROOT_URL.format('downloads')

    # Set up download params
    download_params = {}

    # Set up Plex Pass feed, if active
    if config['plex_pass']:
        download_params['channel'] = 'plexpass'

    # Make API get request to downloads page
    downloads_resp = requests.get(
        downloads_url,
        params=download_params,
        headers={
            'X-Plex-Token': token
        }
    )

    # Decode returned HTML page content
    downloads_xml = html.fromstring(
        downloads_resp.content).xpath('//ul[@class="os"]/li')

    # Set up Linux system search string
    system_search = 'span[@class="linux {0}"]'.format(
        config['linux_system'].lower())

    # Set up Linux version search string
    version_search = 'div/a[@data-event-label="{0}{1}"]'.format(
        config['linux_system'], config['linux_version'])

    # Iterate over HTML content to extract needed info
    for download in downloads_xml:
        # We only care about the Ubuntu Linux version
        if download.find(system_search) is not None:

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
            download_link = download.find(version_search).get('href')

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


def download_update(download, config):
    ''' Download and new Plex package
    '''

    # Set up download name and target path
    download_name = 'pms_{0}.deb'.format(download['version'])
    download_target = os.path.join(config['folder'], download_name)

    # If we've already downloaded this file, remove it
    if os.path.exists(download_target):
        os.remove(download_target)

    # Download the file
    downloader = urllib.URLopener()
    download_path = downloader.retrieve(download['link'], download_target)

    # Make sure the file exists
    return os.path.exists(download_path[0]), download_path[0]


def install_update(package, config):
    ''' Installs the new Plex package
    '''

    try:
        # Install on Ubuntu with dpkg
        if config['linux_system'] == 'Ubuntu':
            subprocess.check_output(
                [DPKG_EXECUTABLE, '-i', package], shell=True)

        # Install on Fedora or CentOS with rpm
        else:
            subprocess.check_output(
                [RPM_EXECUTABLE, '-Uhv', package], shell=True)

    # Check for a failed install
    except subprocess.CalledProcessError:
        return False

    else:
        # Remove the downloaded file, if enabled
        if config['remove_completed']:
            os.remove(package)

        # Return successful
        return True


def main():
    ''' Main wrapper function to run script
    '''

    # Get CLI args
    args = get_args()

    # Get config file info
    config = get_config()

    # Start program
    print('Checking for updates @', time.ctime(), file=sys.stdout)

    # Get token
    token = get_token(config)

    # Get server info
    server = get_server_info(token, args)
    print('Server Version:', server['version'], file=sys.stdout)

    # Get download info
    download = get_download_info(token, config)

    # Check for new version
    if has_newer_version(server, download):
        print('New version available:', download['version'], file=sys.stdout)

        # Bail here if we're not downloading or installing
        if args.check_only:
            return

        # Download the new Plex package
        (download_success, package) = download_update(download, config)

        # Bail here if we had problems with the download
        if not download_success:
            sys.exit('There was an problem downloading the new Plex version.')

        # Bail here if we're only downloading
        if args.skip_install:
            print('The new Plex version has been downloaded:\n',
                  package, file=sys.stdout)
            return

        # Install the new Plex package
        install_success = install_update(package, config)

        # Return an error if there was a problem with the installation
        if not install_success:
            sys.exit('There was an problem installing the new Plex version.')

        # Return success
        print('Plex has been successfully updated to version',
              download['version'], file=sys.stdout)

    else:
        # Otherwise, we have the latest version
        print('Plex is up-to-date!', file=sys.stdout)


if __name__ == '__main__':
    # Run this thing
    main()
