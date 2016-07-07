#!/usr/bin/env python

# Plex Updater
# Copyright (c) 2015-2016 Erin Morelli
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
import xml.etree.ElementTree as ET


# Script global constants
API_ROOT_URL = 'https://plex.tv/{0}'
DPKG_EXECUTABLE = '/usr/bin/dpkg'
RPM_EXECUTABLE = '/usr/bin/rpm'
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
    if 'system_type' not in config.keys():
        sys.exit("Config value for 'system_type' must be defined")

    # Check for valid version value
    if 'system_os' not in config.keys():
        sys.exit("Config value for 'system_os' must be defined")

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
    sign_in_url = API_ROOT_URL.format('users/sign_in.json')

    # Make API post request to get token
    sign_in_resp = requests.post(
        sign_in_url,
        data={},
        headers={
            'X-Plex-Client-Identifier': config['client']
        },
        auth=(config['username'], config['password'])
    )

    # Get JSON response
    sign_in_user = sign_in_resp.json()['user']

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
        subscription = sign_in_user['subscription']

        # If the user has no subscription at all, disable
        if subscription is None:
            disable_plex_pass(config)

        # If the user's subscription is not active, disable
        elif not subscription['active']:
            disable_plex_pass(config)

        # If the user's subscription does not include 'pass', disable
        elif 'pass' not in subscription['features']:
            disable_plex_pass(config)

    # Return token from XML response
    return sign_in_user['authentication_token']


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
    downloads_url = API_ROOT_URL.format('api/downloads/1.json')

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

    # Get JSON content of API return
    downloads = downloads_resp.json()

    # Retrieve system type from config
    system_type = config['system_type'].lower()

    # Make sure system type is valid
    if system_type not in downloads.keys():
        msg = "Value for 'system_type' not valid, must be one of: {0}"
        sys.exit(msg.format(', '.join(downloads.keys())))

    # Get system information
    systems = downloads[system_type]

    # Get system os from config
    system_os = config['system_os']

    # Find matching system OS
    download_os = None
    for available_os in systems.keys():
        if re.match(system_os, available_os, re.I):
            download_os = systems[available_os]
            break

    # Make sure system OS is valid
    if download_os is None:
        msg = "Value for 'system_os' is not valid, "
        msg += "must regex match one of: {0}"
        sys.exit(msg.format(', '.join(systems.keys())))

    # Check for a system build
    if 'system_build' in config.keys():
        # Get system build from config
        system_build = config['system_build']

        # Find a release that matches the build
        download_release = None
        for release in download_os['releases']:
            if re.match(system_build, release['label'], re.I):
                download_release = release
                break

        # Make sure release is valid
        if download_release is None:
            msg = "Value for 'system_build' is not valid, "
            msg += "must regex match one of:\n{0}"
            sys.exit(msg.format("\n".join([
                " + {0}".format(r['label']) for r in download_os['releases']
            ])))
    else:
        # Get our first returned release
        download_release = download_os['releases'][0]

        # Inform user of release being used
        msg = "Using first available {0} build: {1}"
        print(
            msg.format(download_os['name'], download_release['label']),
            file=sys.stdout
        )

    return {
        'updated': download_os['release_date'],
        'version': download_os['version'],
        'link': download_release['url']
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
    download_name = 'pms_{0}{1}'.format(
        download['version'],
        os.path.splitext(download['link'])[1]
    )
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

    # Check for Linux
    if config['system_os'].lower() != 'linux':
        sys.exit('Sorry, installation is currently only available on Linux')

    # Get the package file extension
    package_ext = os.path.splitext(package)[1]

    try:
        # Install on Ubuntu with dpkg
        if (
            re.match(r'deb$', package_ext, re.I) and
            os.path.exists(DPKG_EXECUTABLE)
        ):
            subprocess.check_output(
                '{0} -i {1}'.format(DPKG_EXECUTABLE, package),
                shell=True
            )

        # Install on Fedora or CentOS with rpm
        elif (
            re.match(r'rpm$', package_ext, re.I) and
            os.path.exists(RPM_EXECUTABLE)
        ):
            subprocess.check_output(
                '{0} -Uhv {1}'.format(RPM_EXECUTABLE, package),
                shell=True
            )

    # Check for a failed install
    except subprocess.CalledProcessError:
        return False

    else:
        # Remove the downloaded file, if enabled
        if config['remove_completed']:
            os.remove(package)

        # Return successful
        return True

    # Fall back to false
    return False


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
